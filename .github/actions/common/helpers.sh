#!/bin/bash

_indent_level=0

increase_indent_level() {
    _indent_level=$(( _indent_level + 1 ))
}

decrease_indent_level() {
    _indent_level=$(( _indent_level - 1 ))
}

# Execute a command with optional error handling and output control
#
# Intended to be used to simpify creation of meaningful output and error messages in CI shell scripts
#
# Arguments:
#   --ignore-exit-code|-c <code>  Ignore specific exit code (can be used multiple times)
#   --ignore-errors|-f            Ignore any error exit codes
#   --no-std|-s                   Suppress stdout/stderr output
#   --quiet|-q                    Suppress command execution logging
#   --                            Separator between options and command
#   <command> [args...]           Command and arguments to execute
execute_command() {
    local cmd=()
    local no_std=""
    local quiet=""
    local ignore_exit_codes=()
    local ignore_any_error=""

    if [[ $1 == -* ]]; then
        while [[ $# -gt 0 ]]; do
            case $1 in
                --ignore-exit-code|-c)
                    local code=$2
                    ignore_exit_codes+=("$code")
                    shift
                    shift
                    ;;
                --ignore-errors|-f)
                    ignore_any_error=1
                    shift
                    ;;
                --no-std|-s)
                    no_std=1
                    shift;
                    ;;
                --quiet|-q)
                    quiet=1
                    shift
                    ;;
                --)
                    shift
                    break
                    ;;
                *)
                    console_output 0 red "execute_command error: unknown argument $1, use -- to separate function with command arguments"
                    return 1
                    ;;
            esac
        done
    fi

    while [[ $# -gt 0 ]]; do
        cmd+=("$1")
        shift
    done

    local set_opts=()
    if echo "${SHELLOPTS:-}" | grep -q errexit; then
        set_opts+=("-e")
    fi
    if echo "${SHELLOPTS:-}" | grep -q nounset; then
        set_opts+=("-u")
    fi
    if echo "${SHELLOPTS:-}" | grep -q pipefail; then
        set_opts+=("-o pipefail")
    fi
    if echo "${SHELLOPTS:-}" | grep -q xtrace; then
        set_opts+=("-x")
    fi
    local eval_set=":"
    if [ -n "${set_opts[*]}" ]; then
        eval_set="set ${set_opts[*]}"
    fi

    # Create temporary files for stdout and stderr
    local stdout_file stderr_file
    stdout_file=$(mktemp)
    stderr_file=$(mktemp)

    # Execute command and capture output
    if [ -z "$quiet" ]; then
        console_output 1 gray "Executing command: ${cmd[*]}"
    fi
    increase_indent_level

    set +eu
    (set -euo pipefail; "${cmd[@]}" >"$stdout_file" 2>"$stderr_file")
    last_cmd_result=$?
    eval "$eval_set"

    # Read captured output
    last_cmd_stdout=$(cat "$stdout_file")
    last_cmd_stderr=$(cat "$stderr_file")

    if [ -z "$no_std" ]; then
        cat "$stdout_file"
        cat "$stderr_file" >&2
    fi

    # Clean up temporary files
    rm -f "$stdout_file" "$stderr_file"

    if [ "$last_cmd_result" -ne 0 ] && [ -z "$ignore_any_error" ] && [[ ! " ${ignore_exit_codes[*]} " =~ [[:space:]]${last_cmd_result}[[:space:]] ]] && [ -z "$quiet" ]; then
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

    decrease_indent_level
    return $last_cmd_result
}

init_console_output() {
    if [[ ! -e /dev/fd/9 ]]; then
        # Let nested console_output to always output into stderr, even if it's redirected
        exec 9>&2
    fi
}

# Helper function to output multiline variables with color
# If fd 9 is opened will write to it else to stderr
console_output() {
    indent=$(( $_indent_level*2 ))
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
            if [[ -e /dev/fd/9 ]]; then
                printf "${color_code}%*s%s${reset_code}\n" $indent "" "$line" >&9
            else
                printf "${color_code}%*s%s${reset_code}\n" $indent "" "$line" >&2
            fi
        done <<< "$content"
    fi
}
