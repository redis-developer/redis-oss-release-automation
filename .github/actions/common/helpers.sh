# Function to execute command from array and capture output
execute_command() {
    # turn off errexit (set -e) if it is active and restore it later
    echo $SHELLOPTS | grep -q errexit && restore_errexit="1" && set +e || restore_errexit=""

    local cmd

    # Check if no arguments provided
    if [ $# -eq 0 ]; then
        # Check if cmd_array variable exists and is an array
        if declare -p cmd_array 2>/dev/null | grep -q "declare -a"; then
            # Use the existing cmd_array variable
            cmd=("${cmd_array[@]}")
        else
            echo "Error: No arguments provided and cmd_array variable not found or not an array" >&2
            return 1
        fi
    else
        cmd=("$@")
    fi

    # Create temporary files for stdout and stderr
    local stdout_file stderr_file
    stdout_file=$(mktemp)
    stderr_file=$(mktemp)

    # Execute command and capture output
    console_output 1 gray "Executing command: ${cmd[*]}"

    "${cmd[@]}" >"$stdout_file" 2>"$stderr_file"
    last_cmd_result=$?

    # Read captured output
    last_cmd_stdout=$(cat "$stdout_file")
    last_cmd_stderr=$(cat "$stderr_file")


    if [ "$last_cmd_result" -ne 0 ]; then
        console_output 0 red "Command failed with exit code $last_cmd_result"
        if [ -n "$last_cmd_stderr" ]; then
            console_output 0 red "Standard Error:"
            console_output 0 red "$last_cmd_stderr"
        fi
        if [ -n "$last_cmd_stdout" ]; then
            console_output 0 red "Standard Output:"
            console_output 0 red "$last_cmd_stdout"
        fi
    fi

    # Clean up temporary files
    rm -f "$stdout_file" "$stderr_file"

    [ "$restore_errexit" ] && set -e
    return $last_cmd_result
}

# Helper function to output multiline variables with color
console_output() {
    local verbosity_level="$1"
    local color="$2"
    local content="$3"
    local current_verbosity="${VERBOSITY:-0}"

    # Check if we should output based on verbosity level
    if [ "$current_verbosity" -ge "$verbosity_level" ]; then
        local color_code=""
        local reset_code="\033[0m"

        case "$color" in
            "gray"|"grey")
                color_code="\033[90m"
                ;;
            "white")
                color_code="\033[97m"
                ;;
            "red")
                color_code="\033[91m"
                ;;
            *)
                color_code="\033[0m"  # Default to no color
                ;;
        esac

        # Output each line with 4-space indent and color
        while IFS= read -r line || [ -n "$line" ]; do
            printf "${color_code}    %s${reset_code}\n" "$line"
        done <<< "$content"
    fi
}
