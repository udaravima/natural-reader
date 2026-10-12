# Windows bare-metal fixes (console encoding, event loop) before anything
# else in the package prints or opens a database connection.
from . import platform_compat

platform_compat.apply()
