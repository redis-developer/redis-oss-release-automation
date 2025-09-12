#!/bin/bash
SCRIPT_DIR="$(dirname -- "$( readlink -f -- "$0"; )")"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/../.github/actions/common/helpers.sh"

last_cmd_result=0
last_cmd_stdout=""
last_cmd_stderr=""

test_execute_command_args_with_spaces() {
    execute_command -- mktemp 'file with spaces XXXXXXXX' >/dev/null 2>/dev/null
    assertTrue "File created" "[ -f '$last_cmd_stdout' ]"
    if [ -n "$last_cmd_stdout" ] && [ -f "$last_cmd_stdout" ]; then
        rm -f "$last_cmd_stdout"
    fi
}

test_execute_command_std() {
    # shellcheck disable=SC2329
    tfun() {
        echo "Hello World"
        echo "Goodbye World" >&2
    }
    local stdout=$(execute_command tfun 2>/dev/null)
    local stderr=$(execute_command tfun 2>&1 >/dev/null)
    assertEquals "stdout" "Hello World" "$stdout"
    assertEquals "stderr" "Goodbye World" "$stderr"
}

test_execute_command_no_std() {
    # shellcheck disable=SC2329
    tfun() {
        echo "Hello World"
        echo "Goodbye World" >&2
    }

    # Create temporary files for stdout and stderr
    local stdout_file stderr_file
    stdout_file=$(mktemp)
    stderr_file=$(mktemp)

    last_cmd_result=100
    execute_command --no-std -- tfun 1>"$stdout_file" 2>"$stderr_file"

    # Read captured output
    stdout=$(cat "$stdout_file")
    stderr=$(cat "$stderr_file")

    rm -f "$stdout_file" "$stderr_file"

    assertEquals "stdout" "" "$stdout"
    assertEquals "stderr" "" "$stderr"

    assertEquals "last_cmd_stdout" "Hello World" "$last_cmd_stdout"
    assertEquals "last_cmd_stderr" "Goodbye World" "$last_cmd_stderr"
    assertEquals "last_cmd_result" 0 "$last_cmd_result"
}

test_execute_command_fail() {
    # shellcheck disable=SC2329
    tfun() {
        echo "Hello World"
        return 2
    }
    local stderr=$(execute_command tfun 2>&1 >/dev/null || :)
    assertContains "Error message in stderr" "$stderr" "Command failed with exit code 2"
    assertContains "Command output in stderr" "$stderr" "Hello World"
}

test_execute_command_ignore_errors() {
    # shellcheck disable=SC2329
    tfun() {
        echo "Hello World"
        return 2
    }
    execute_command --no-std --ignore-errors -- tfun
    assertEquals "last_cmd_result" 2 "$last_cmd_result"
    execute_command --no-std -f -- tfun
    assertEquals "last_cmd_result" 2 "$last_cmd_result"
}

test_execute_command_ignore_error_code() {
    # shellcheck disable=SC2329
    tfun() {
        echo "Hello World"
        return 111
    }

    # Create temporary files for stdout and stderr
    local stdout_file stderr_file
    stdout_file=$(mktemp)
    stderr_file=$(mktemp)
    execute_command --no-std --ignore-exit-code 111 -- tfun 1>"$stdout_file" 2>"$stderr_file"
    assertEquals "last_cmd_result" 111 "$last_cmd_result"
    # Read captured output
    stdout=$(cat "$stdout_file")
    stderr=$(cat "$stderr_file")
    rm -f "$stdout_file" "$stderr_file"
    # Assert no output
    assertEquals "stdout" "" "$stdout"
    assertEquals "stderr" "" "$stderr"

    # Create temporary files for stdout and stderr
    local stdout_file stderr_file
    stdout_file=$(mktemp)
    stderr_file=$(mktemp)
    execute_command --no-std -c 111 -- tfun
    assertEquals "last_cmd_result" 111 "$last_cmd_result" 1>"$stdout_file" 2>"$stderr_file"
    # Read captured output
    stdout=$(cat "$stdout_file")
    stderr=$(cat "$stderr_file")
    rm -f "$stdout_file" "$stderr_file"
    # Assert no output
    assertEquals "stdout" "" "$stdout"
    assertEquals "stderr" "" "$stderr"
}

test_execute_command_ignore_error_code_fail() {
    # shellcheck disable=SC2329
    tfun() {
        echo "Hello World"
        return 111
    }

    # Create temporary files for stdout and stderr
    local stdout_file stderr_file
    stdout_file=$(mktemp)
    stderr_file=$(mktemp)
    execute_command --no-std --ignore-exit-code 222 -- tfun 1>"$stdout_file" 2>"$stderr_file"
    assertEquals "last_cmd_result" 111 "$last_cmd_result"
    # Read captured output
    stdout=$(cat "$stdout_file")
    stderr=$(cat "$stderr_file")
    rm -f "$stdout_file" "$stderr_file"
    # Assert no output
    assertEquals "stdout" "" "$stdout"
    assertContains "stderr" "$stderr" "Command failed with exit code 111"

    # Create temporary files for stdout and stderr
    local stdout_file stderr_file
    stdout_file=$(mktemp)
    stderr_file=$(mktemp)
    execute_command --no-std -c 222 -- tfun 1>"$stdout_file" 2>"$stderr_file"
    assertEquals "last_cmd_result" 111 "$last_cmd_result"
    # Read captured output
    stdout=$(cat "$stdout_file")
    stderr=$(cat "$stderr_file")
    rm -f "$stdout_file" "$stderr_file"
    # Assert no output
    assertEquals "stdout" "" "$stdout"
    assertContains "stderr" "$stderr" "Command failed with exit code 111"
}

test_execute_command_quiet() {
    # shellcheck disable=SC2329
    tfun() {
        return 1
    }
    local stdout_file stderr_file
    stdout_file=$(mktemp)
    stderr_file=$(mktemp)
    execute_command -q -- tfun 1>"$stdout_file" 2>"$stderr_file"
    assertEquals "last_cmd_result" 1 "$last_cmd_result"
    # Read captured output
    stdout=$(cat "$stdout_file")
    stderr=$(cat "$stderr_file")
    rm -f "$stdout_file" "$stderr_file"
    # Assert no output
    assertEquals "stdout" "" "$stdout"
    assertEquals "stderr" "" "$stderr"
}


# shellcheck disable=SC1091
. "$SCRIPT_DIR/shunit2"