#!/bin/bash
# Claude Code Status Line — detailed token/cost/rate display
# Receives JSON via stdin with session metadata

input=$(cat)

# Model
model_id=$(echo "$input" | jq -r '.model.id // ""')
if [[ "$model_id" == *"opus"* ]]; then
    model="Opus 4.6"
elif [[ "$model_id" == *"sonnet"* ]]; then
    model="Sonnet 4.5"
elif [[ "$model_id" == *"haiku"* ]]; then
    model="Haiku 4.5"
else
    model=$(echo "$input" | jq -r '.model.display_name // "Unknown"')
fi

# Context window
total_input=$(echo "$input" | jq -r '.context_window.total_input_tokens // 0')
total_output=$(echo "$input" | jq -r '.context_window.total_output_tokens // 0')
context_size=$(echo "$input" | jq -r '.context_window.context_window_size // 0')
used_pct=$(echo "$input" | jq -r '.context_window.used_percentage // 0')
remaining_pct=$(echo "$input" | jq -r '.context_window.remaining_percentage // 100')

total_used=$((total_input + total_output))
remaining=$((context_size - total_used))

fmt() {
    local n=$1
    if [ "$n" -ge 1000000 ]; then
        printf "%.1fM" "$(echo "scale=1; $n/1000000" | bc)"
    elif [ "$n" -ge 1000 ]; then
        echo "$((n / 1000))k"
    else
        echo "$n"
    fi
}

used_pct_int=$(printf "%.0f" "$used_pct")
remain_pct_int=$(printf "%.0f" "$remaining_pct")

# Thinking mode
thinking=$(echo "$input" | jq -r '.thinking.enabled // false')
if [ "$thinking" = "true" ]; then
    think_str="thinking: On"
else
    think_str="thinking: Off"
fi

# Balance usage
BALANCE_DIR="$HOME/.claude/hooks"
BALANCE_CFG="$BALANCE_DIR/balance.json"
USAGE_FILE="$BALANCE_DIR/.usage/$(date +%Y-%m-%d).log"
balance_str=""

if [ -f "$BALANCE_CFG" ]; then
    # Active minutes today
    if [ -f "$USAGE_FILE" ]; then
        active_min=$(sort -u "$USAGE_FILE" | wc -l | tr -d ' ')
    else
        active_min=0
    fi

    # Current time in minutes since midnight
    cur_h=$(date +%H | sed 's/^0//')
    cur_m=$(date +%M | sed 's/^0//')
    cur_mins=$(( cur_h * 60 + cur_m ))

    # Find today's schedule
    dow=$(date +%u)
    sched_json=$(jq -r --argjson dow "$dow" '
        .schedule | to_entries[] |
        select(.value.days | index($dow)) |
        .value
    ' "$BALANCE_CFG" 2>/dev/null)

    # Check for active override (extension in use)
    OVERRIDE_FILE="$HOME/.balance_override"
    override_str=""
    override_active=false
    if [ -f "$OVERRIDE_FILE" ]; then
        ov_expires=$(jq -r '.expires_at // empty' "$OVERRIDE_FILE" 2>/dev/null)
        ov_label=$(jq -r '.label // "override"' "$OVERRIDE_FILE" 2>/dev/null)
        if [ -n "$ov_expires" ]; then
            # Compare expiry to now (epoch seconds)
            ov_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%S" "${ov_expires%%.*}" +%s 2>/dev/null || date -d "${ov_expires}" +%s 2>/dev/null)
            now_epoch=$(date +%s)
            if [ -n "$ov_epoch" ] && [ "$now_epoch" -lt "$ov_epoch" ]; then
                ov_remaining=$(( (ov_epoch - now_epoch) / 60 ))
                override_str=" \033[36m+${ov_label} (${ov_remaining}m left)\033[0m"
                override_active=true
            fi
        fi
    fi

    # Count extensions used today
    EXT_FILE="$BALANCE_DIR/.usage/$(date +%Y-%m-%d).extensions.json"
    ext_str=""
    if [ -f "$EXT_FILE" ]; then
        ext_total=$(jq '[.[] | numbers] | add // 0' "$EXT_FILE" 2>/dev/null)
        if [ "${ext_total:-0}" -gt 0 ]; then
            ext_str=" ext:${ext_total}"
        fi
    fi

    if [ -n "$sched_json" ] && [ "$sched_json" != "null" ]; then
        daily_limit=$(echo "$sched_json" | jq -r '.daily_limit_minutes // 240')
        remaining_cap=$((daily_limit - active_min))
        [ "$remaining_cap" -lt 0 ] && remaining_cap=0

        # Find active window and time to window end
        window_info=$(echo "$sched_json" | jq -r --argjson cm "$cur_mins" '
            .windows[] |
            select(
                ($cm >= ((.start | split(":") | .[0] | tonumber) * 60 + (.start | split(":") | .[1] | tonumber))) and
                ($cm < ((.end | split(":") | .[0] | tonumber) * 60 + (.end | split(":") | .[1] | tonumber)))
            ) |
            "\(.start)-\(.end)|\(((.end | split(":") | .[0] | tonumber) * 60 + (.end | split(":") | .[1] | tonumber)) - $cm)"
        ' 2>/dev/null | head -1)

        if [ -n "$window_info" ]; then
            window_range=$(echo "$window_info" | cut -d'|' -f1)
            mins_to_window_end=$(echo "$window_info" | cut -d'|' -f2)

            # Warning thresholds from config
            warn_end=$(jq -r '.warning_minutes_before_end // 15' "$BALANCE_CFG")
            warn_cap=$(jq -r '.warning_minutes_before_cap // 30' "$BALANCE_CFG")

            # Build warning string
            warn=""
            if [ "$remaining_cap" -le 0 ]; then
                warn=" \033[31m!! CAP REACHED !!\033[0m"
            elif [ "$remaining_cap" -le "$warn_cap" ]; then
                warn=" \033[33m! ${remaining_cap}m to cap\033[0m"
            fi

            if [ "$mins_to_window_end" -le "$warn_end" ] && [ -z "$warn" ]; then
                warn=" \033[33m! window ends in ${mins_to_window_end}m\033[0m"
            elif [ "$mins_to_window_end" -le "$warn_end" ] && [ -n "$warn" ]; then
                warn="${warn} \033[33m| window ${mins_to_window_end}m\033[0m"
            fi

            balance_str=" | Bal: ${active_min}/${daily_limit}m${ext_str} [${window_range}]${warn}${override_str}"
        else
            # Outside windows
            if [ "$override_active" = true ]; then
                # Extended session — show override instead of "next window"
                balance_str=" | Bal: ${active_min}/${daily_limit}m${ext_str}${override_str}"
            else
                next_win=$(echo "$sched_json" | jq -r --argjson cm "$cur_mins" '
                    [.windows[] |
                    select(
                        ((.start | split(":") | .[0] | tonumber) * 60 + (.start | split(":") | .[1] | tonumber)) > $cm
                    ) | .start] | first // empty
                ' 2>/dev/null)

                if [ -n "$next_win" ]; then
                    balance_str=" | Bal: ${active_min}/${daily_limit}m${ext_str} \033[90m(next: ${next_win})\033[0m"
                else
                    balance_str=" | Bal: ${active_min}/${daily_limit}m${ext_str} \033[90m(done for today)\033[0m"
                fi
            fi
        fi
    else
        # No schedule for today
        if [ "$override_active" = true ]; then
            balance_str=" | Bal: ${active_min}m${ext_str}${override_str}"
        else
            balance_str=" | Bal: \033[90mno schedule today\033[0m"
        fi
    fi
fi

# Build output
printf "%s | %s / %s | %s%% used | %s%% remain | %s%s" \
    "$model" \
    "$(fmt $total_used)" "$(fmt $context_size)" \
    "$used_pct_int" \
    "$remain_pct_int" \
    "$think_str" \
    "$balance_str"
