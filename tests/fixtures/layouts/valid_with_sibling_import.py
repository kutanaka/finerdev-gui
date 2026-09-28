from _sibling_helper import make_instance

from devgui import Category, Device

layout = [
    Category("Power", [Device("PSU1", make_instance(), widgets=[])]),
]
