#Oh-my-ZSH Matrix plugin based on Corey Golderg Python script : http://coreygoldberg.blogspot.com/2013/01/python-matrix-in-your-terminal.html

# Path to plugin directory (absolute)
_MATRIX_DIR="${0:A:h}"

# Choose available Python interpreter (python3 or python)
if command -v python3 >/dev/null 2>&1; then
    _MATRIX_PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    _MATRIX_PYTHON="python"
else
    _MATRIX_PYTHON="python3"
fi

matrix() {
    "${_MATRIX_PYTHON}" "${_MATRIX_DIR}/matrix.py"
}
