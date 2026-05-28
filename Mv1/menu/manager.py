# menu/manager.py - JSON-driven navigation only
import ujson as json


class MenuManager:
    """Three-row JSON menu.

    This class intentionally does not import or execute feature modules. It
    returns selected module descriptors to the kernel dispatcher.
    """

    def __init__(self, oled, config_path="menu_config.json"):
        self.oled = oled
        self.config_path = config_path
        self.full_config = {}
        self.current_menu_id = "main"
        self.index = 0
        self.history = []
        self.in_action = False

    def load_config(self):
        with open(self.config_path, "r") as f:
            self.full_config = json.load(f)
        if self.current_menu_id not in self.full_config:
            raise ValueError("main menu is missing")

    def current_items(self):
        return self.full_config.get(self.current_menu_id, {}).get("items", ())

    def draw(self):
        if not self.in_action:
            self.oled.show_menu(self.current_items(), self.index)

    def handle(self, action):
        """Apply navigation action; return module descriptor or None."""
        if self.in_action:
            return None

        items = self.current_items()
        if not items:
            return None

        if action == "up":
            self.index = (self.index - 1) % len(items)
        elif action == "down":
            self.index = (self.index + 1) % len(items)
        elif action == "back":
            self.back()
        elif action == "enter":
            return self._select(items[self.index])

        self.draw()
        return None

    def _select(self, item):
        itype = item.get("type")
        if itype == "submenu":
            self.history.append((self.current_menu_id, self.index))
            self.current_menu_id = item["target"]
            self.index = 0
            self.draw()
        elif itype == "back":
            self.back()
            self.draw()
        elif itype == "module":
            return {
                "module": item.get("module"),
                "entry": item.get("entry"),
                "name": item.get("name", item.get("module", "module")),
            }
        return None

    def back(self):
        if self.history:
            self.current_menu_id, self.index = self.history.pop()

    def enter_action_mode(self):
        self.in_action = True

    def exit_action_mode(self):
        self.in_action = False
        self.draw()
