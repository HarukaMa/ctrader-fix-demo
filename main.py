
import logging
import curses
import re
from decimal import Decimal
from operator import itemgetter

from fix import FIX, Side, OrderType

fix: FIX
global dom1, dom2, dom3
global pos, order
subs = {}

def main(screen):
    screen.keypad(True)
    curses.echo()
    curses.start_color()
    curses.use_default_colors()
    for i in range(0, curses.COLORS):
        curses.init_pair(i + 1, i, -1)
    dim = screen.getmaxyx()
    global dom1, dom2, dom3
    dom1 = curses.newwin(7, 42, 1, 3)
    dom1.border()
    dom1.refresh()
    dom2 = curses.newwin(7, 42, 1, 48)
    dom2.border()
    dom2.refresh()
    dom3 = curses.newwin(7, 42, 1, 93)
    dom3.border()
    dom3.refresh()
    global pos
    pos = curses.newwin(8, dim[1] - 6, 8, 3)
    pos.border()
    pos.refresh()
    global order
    order = curses.newwin(8, dim[1] - 6, 16, 3)
    order.border()
    order.refresh()
    cmd = curses.newwin(1, 80)
    log = curses.newwin(dim[0] - 25, dim[1], 25, 0)
    log.idlok(True)
    log.scrollok(True)
    handler = LoggingHandler(log)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
    handler.setLevel(logging.INFO)
    logging.getLogger().setLevel(logging.DEBUG)
    logging.getLogger().addHandler(handler)
    cmd.move(0, 0)
    global fix
    fix = FIX("server", "broker", "account", "password", "account_currency", position_list_callback, order_list_callback)
    while True:
        cmd.move(0, 0)
        cmd.clrtoeol()
        parse_command(cmd.getstr(0, 0).decode())


def parse_command(command: str):
    parts = command.split(" ")
    logging.debug(parts)
    logging.info("Command: %s" % command)
    if parts[0] == "sub":
        try:
            if (subid := int(parts[1])) < 1 or int(parts[1]) > 3:
                logging.error("Subscription ID out of range")
            subs[subid] = parts[2].upper()
            fix.market_request(subid - 1, parts[2].upper(), quote_callback)
        except ValueError:
            logging.error("Invalid subscription ID")
    if parts[0] in ["buy", "sell"]:
        if parts[1] in ["stop", "limit"]:
            fix.new_limit_order(
                parts[2].upper(),
                Side.Buy if parts[0] == "buy" else Side.Sell,
                OrderType.Limit if parts[1] == "limit" else OrderType.Stop,
                float(parts[3]),
                float(parts[4]),
                parts[5] if len(parts) == 6 else None
            )
        else:
            fix.new_market_order(
                parts[1].upper(),
                Side.Buy if parts[0] == "buy" else Side.Sell,
                float(parts[2]),
                parts[3] if len(parts) == 4 else None
            )
    if parts[0] == "close":
        fix.close_position(parts[1])
    if parts[0] == "cancel":
        fix.cancel_order(parts[1])

def float_format(fmt: str, num: float, force_sign = True):
    return max(('{:+}' if force_sign else "{}").format(round(num, 6)), fmt.format(num), key=len)

def position_list_callback(data: dict, price_data: dict):
    pos.erase()
    pos.border()
    for i, kv in enumerate(data.items()):
        pos_id = kv[0]
        name = kv[1]["name"]
        side = "Buy" if kv[1]["long"] > 0 else "Sell"
        amount = kv[1]["long"] if kv[1]["long"] > 0 else kv[1]["short"]
        price = float_format("{:.%df}" % kv[1]["digits"], kv[1]["price"], False)
        pos.addstr(i + 1, 2, pos_id)
        pos.addstr(i + 1, 12, name)
        pos.addstr(i + 1, 22, side)
        pos.addstr(i + 1, 30, str(amount))
        pos.addstr(i + 1, 40, price)
        if price := price_data.get(name, None):
            if side == "Buy":
                p = price["bid"]
            else:
                p = price["offer"]
            pos.addstr(i + 1, 52, ("{:.%df}" % kv[1]["digits"]).format(p))
            diff = p - kv[1]["price"]
            if side == "Sell":
                diff = -diff
            if diff > 0:
                color = curses.color_pair(11)
            elif diff < 0:
                color = curses.color_pair(10)
            else:
                color = curses.color_pair(0)
            pos.addstr(i + 1, 62, float_format("{:+.%df}" % kv[1]["digits"], diff), color)
            pl = amount * diff
            pos.addstr(i + 1, 74, float_format("{:+.2f}", pl), color)
            convert = kv[1]["convert"]
            convert_dir = kv[1]["convert_dir"]
            if price := price_data.get(convert, None):
                if convert_dir:
                    rate = 1 / price["offer"]
                else:
                    rate = price["bid"]
                pl_base = pl * rate
                pos.addstr(i + 1, 86, "{:+.2f}".format(round(pl_base, 2)), color)
    pos.refresh()

def order_list_callback(data: dict, price_data: dict):
    order.erase()
    order.border()
    for i, kv in enumerate(data.items()):
        ord_id = kv[0]
        name = kv[1]["name"]
        side = "Buy" if kv[1]["side"] == Side.Buy else "Sell"
        order_type = kv[1]["type"]
        order.addstr(i + 1, 2, ord_id)
        order.addstr(i + 1, 12, name)
        order.addstr(i + 1, 22, side)
        order.addstr(i + 1, 30, str(kv[1]["amount"]))
        if order_type > 1:
            order.addstr(i + 1, 40, float_format("{:.%df}" % kv[1]["digits"], kv[1]["price"], False))
        if price := price_data.get(name, None):
            if side == "Buy":
                price = price["offer"]
            else:
                price = price["bid"]
            order.addstr(i + 1, 50, float_format("{:.%df}" % kv[1]["digits"], price, False))
        if pos_id := kv[1]["pos_id"]:
            order.addstr(i + 1, 60, pos_id)
        order.addstr(i + 1, 70, kv[1]["clid"])
    order.refresh()


def quote_callback(name: str, digits: int, data: dict):
    dom = None
    for subid, symbol in subs.items():
        if name == symbol:
            if subid == 1:
                dom = dom1
            elif subid == 2:
                dom = dom2
            elif subid == 3:
                dom = dom3
    if not dom:
        return
    dom.erase()
    dom.border()
    dom.addstr(0, 21 - len(name) // 2 - 1, " %s " % name)
    offer = []
    bid = []
    for e in data.values():
        if e["type"] == 0:
            bid.append(e)
        else:
            offer.append(e)
    offer.sort(key=itemgetter("price"))
    bid.sort(key=itemgetter("price"), reverse=True)
    for i, e in enumerate(bid):
        p = ("{:.%df}" % digits).format(e["price"])
        dom.addstr(i + 1, 20 - len(p), p)
        if "size" in e.keys():
            a = str(e["size"])
            dom.addstr(i + 1, 2, a)
    for i, e in enumerate(offer):
        p = ("{:.%df}" % digits).format(e["price"])
        dom.addstr(i + 1, 22, p)
        if "size" in e.keys():
            a = str(e["size"])
            dom.addstr(i + 1, 40 - len(a), a)
    dom.refresh()

def addstr_color(window, msg: str):
    parts = msg.split("\033")
    for p in parts:
        if match := re.search(r"\[(\d+)m", p):
            code = int(match.group(1))
            if 30 <= code <= 37:
                color = curses.color_pair(code - 29)
            elif 90 <= code <= 97:
                color = curses.color_pair(code - 89 + 8)
            else:
                color = curses.color_pair(0)
            window.addstr(p[match.span()[1]:], color)
        else:
            window.addstr(p)

class LoggingHandler(logging.Handler):
    def __init__(self, window):
        super().__init__()
        self.window = window

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            addstr_color(self.window, "%s\n" % msg)
            self.window.refresh()
        except (KeyboardInterrupt, SystemExit):
            raise


if __name__ == '__main__':
    # https://stackoverflow.com/a/7995762 for quick log message coloring
    logging.addLevelName(logging.WARNING, "\033[91m%s\033[0m" % logging.getLevelName(logging.WARNING))
    logging.addLevelName(logging.ERROR, "\033[101m%s\033[0m" % logging.getLevelName(logging.ERROR))
    curses.wrapper(main)