import logging
import re
import threading
from datetime import datetime
import time
from enum import IntEnum, Enum
import socket
from pprint import pformat

from buffer import Buffer

class Field(IntEnum):
    AvgPx = 6
    BeginString = 8
    BodyLength = 9
    CheckSum = 10
    ClOrdId = 11
    CumQty = 14
    MsgSeqNum = 34
    MsgType = 35
    OrderID = 37
    OrderQty = 38
    OrdStatus = 39
    OrdType = 40
    OrigClOrdID = 41
    Price = 44
    RefSeqNum = 45
    SenderCompID = 49
    SenderSubID = 50
    SendingTime = 52
    Side = 54
    Symbol = 55
    TargetCompID = 56
    TargetSubID = 57
    Text = 58
    TimeInForce = 59
    TransactTime = 60
    EncryptMethod = 98
    StopPx = 99
    OrdRejReason = 103
    HeartBtInt = 108
    TestReqID = 112
    ExpireTime = 126
    ResetSeqNumFlag = 141
    NoRelatedSym = 146
    ExecType = 150
    LeavesQty = 151
    MDReqID = 262
    SubscriptionRequestType = 263
    MarketDepth = 264
    MDUpdateType = 265
    NoMDEntryTypes = 267
    NoMDEntries = 268
    MDEntryType = 269
    MDEntryPx = 270
    MDEntrySize = 271
    MDEntryID = 278
    MDUpdateAction = 279
    SecurityReqID = 320
    SecurityResponseID = 322
    EncodedTextLen = 354
    EncodedText = 355
    RefTagID = 371
    RefMsgType = 372
    SessionRejectReason = 373
    BusinessRejectRefID = 379
    BusinessRejectReason = 380
    Designation = 494
    Username = 553
    Password = 554
    SecurityListRequestType = 559
    SecurityRequestResult = 560
    MassStatusReqID = 584
    MassStatusReqType = 585
    NoPositions = 702
    LongQty = 704
    ShortQty = 705
    PosReqID = 710
    PosMaintRptID = 721
    TotalNumPosReports = 727
    PosReqResult = 728
    SettlPrice = 730
    TotNumReports = 911
    AbsoluteTP = 1000
    RelativeTP = 1001
    AbsoluteSL = 1002
    RelativeSL = 1003
    TrailingSL = 1004
    TriggerMethodSL = 1005
    GuaranteedSL = 1006
    SymbolName = 1007
    SymbolDigits = 1008


class SubID(Enum):
    QUOTE = "QUOTE"
    TRADE = "TRADE"

    def __str__(self):
        return self.value

class Side(IntEnum):
    Buy = 1
    Sell = 2

class OrderType(IntEnum):
    Market = 1
    Limit = 2
    Stop = 3

def get_date():
    return datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3]

class FIX:

    class Message:

        def __init__(self, sub: SubID = None, msg_type: str = None, parent = None):
            self.fields = []
            if parent:
                self.origin = True
                self.fields.append((Field.BeginString, "FIX.4.4"))
                self.fields.append((Field.BodyLength, 0))
                self.fields.append((Field.MsgType, msg_type))
                self.fields.append((Field.SenderCompID, parent.broker + "." + parent.login))
                self.fields.append((Field.SenderSubID, sub))
                self.fields.append((Field.TargetCompID, "CSERVER"))
                self.fields.append((Field.TargetSubID, sub))
                if sub == SubID.QUOTE:
                    self.fields.append((Field.MsgSeqNum, parent.qseq))
                    parent.qseq += 1
                elif sub == SubID.TRADE:
                    self.fields.append((Field.MsgSeqNum, parent.tseq))
                    parent.tseq += 1
                self.fields.append((Field.SendingTime, get_date()))
            else:
                self.origin = False

        def __getitem__(self, item):
            for k, v in self.fields:
                if k == item:
                    return v
            return None

        def __setitem__(self, key, value):
            self.fields.append((key, value))

        def get_repeating_groups(self, count_key, repeating_start, repeating_end = None):
            count = None
            result = []
            item = {}
            for k, v in self.fields[8:]:
                if count == 0:
                    return result
                if count is None:
                    if k == count_key:
                        count = int(v)
                    continue
                if (k == repeating_start and len(item) > 0) or k == repeating_end:
                    result.append(item)
                    item = {}
                    count -= 1
                item[k] = v
            result.append(item)
            return result

        def __bytes__(self):
            data = bytearray()
            for k, v in self.fields:
                data.extend(b"%b=%b\x01" % (str(k.value).encode(), str(v).encode()))
            if self.origin:
                data[12:13] = b"%d" % (len(data) - 14)
                cksm = sum(data) % 256
                data.extend(b"10=%03d\x01" % cksm)
            return bytes(data)

        def __str__(self):
            data = ""
            for k, v in self.fields:
                data += "%s=%s|" % (str(k.value), str(v))
            if self.origin:
                data = data[:12] + "%d" % (len(data) - 14) + data[13:]
                cksm = sum(data.replace("|", "\x01").encode()) % 256
                data += "10=%03d|" % cksm
            return data

        def __repr__(self):
            return pformat([(k.name, v) for k, v in self.fields])

    def __init__(self, server: str, broker: str, login: str, password: str, currency: str, position_list_callback, order_list_callback):
        self.qstream = Buffer()
        self.qs = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.qs.connect((server, 5201))
        self.tstream = Buffer()
        self.ts = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.ts.connect((server, 5202))
        self.broker = broker
        self.login = login
        self.password = password
        self.currency = currency
        self.qseq = 1
        self.tseq = 1
        self.qtest_seq = 1
        self.ttest_seq = 1
        self.market_seq = 1
        self.subscribed_symbol = [-1, -1, -1]
        self.qworker_thread = threading.Thread(target=self.qworker)
        self.qworker_thread.start()
        self.tworker_thread = threading.Thread(target=self.tworker)
        self.tworker_thread.start()
        self.ping_qworker_thread = None
        self.ping_tworker_thread = None
        self.sec_list_callback = None
        self.market_callback = None
        self.sec_id_table = {}
        self.sec_name_table = {}
        self.position_list_callback = position_list_callback
        self.order_list_callback = order_list_callback
        self.market_data = {}
        self.position_list = {}
        self.spot_request_list = set()
        self.spot_price_list = {}
        self.base_convert_request_list = set()
        self.base_convert_list = {}
        self.order_list = {}
        self.logon()
        self.sec_list()

    def qworker(self):
        while True:
            data = self.qs.recv(65535)
            if len(data) == 0:
                logging.error("Disconnected")
                break
            self.qstream.write(data)
            self.parse_quote_message()

    def tworker(self):
        while True:
            data = self.ts.recv(65535)
            if len(data) == 0:
                logging.error("Disconnected")
                break
            self.tstream.write(data)
            self.parse_trade_message()

    def parse_quote_message(self):
        while len(self.qstream) > 0:
            match = re.search(rb"10=\d{3}\x01", self.qstream.peek(self.qstream.count()))
            if match:
                msg = FIX.Message()
                data = self.qstream.read(match.span()[1]).split(b"\x01")[:-1]
                for part in data:
                    tag, value = part.split(b"=", 1)
                    msg[Field(int(tag.decode()))] = value.decode()
                logging.debug("\033[32mRECV <<< %s\033[0m" % msg)
                self.process_message(msg)
            else:
                break

    def parse_trade_message(self):
        while len(self.tstream) > 0:
            match = re.search(rb"10=\d{3}\x01", self.tstream.peek(self.tstream.count()))
            if match:
                msg = FIX.Message()
                data = self.tstream.read(match.span()[1]).split(b"\x01")[:-1]
                for part in data:
                    tag, value = part.split(b"=", 1)
                    msg[Field(int(tag.decode()))] = value.decode()
                logging.debug("\033[92mRECV <<< %s\033[0m" % msg)
                self.process_message(msg)
            else:
                break

    def ping_qworker(self, interval: int):
        while True:
            self.qheartbeat()
            time.sleep(interval)

    def ping_tworker(self, interval: int):
        while True:
            self.theartbeat()
            time.sleep(interval)

    def process_ping(self, msg):
        pass

    def process_test(self, msg):
        if msg[Field.SenderSubID] == "QUOTE":
            self.qheartbeat(msg[Field.TestReqID])
        elif msg[Field.SenderSubID] == "TRADE":
            self.theartbeat(msg[Field.TestReqID])

    def process_logout(self, msg):
        logging.error("Logged out: %s" % msg[Field.Text])

    def process_exec_report(self, msg):
        if msg[Field.ExecType] == "F":
            self.position_list = {}
            self.position_request()
            self.order_list = {}
            self.order_request()
        elif msg[Field.ExecType] in ["0", "4", "5", "C"]:
            self.order_list = {}
            self.order_request()
        elif msg[Field.ExecType] == "I":
            name = self.sec_id_table[int(msg[Field.Symbol])]["name"]
            self.order_list[msg[Field.OrderID]] = {
                "name": name,
                "side": Side(int(msg[Field.Side])),
                "amount": float(msg[Field.LeavesQty]),
                "type": int(msg[Field.OrdType]),
                "pos_id": msg[Field.PosMaintRptID],
                "digits": self.sec_id_table[int(msg[Field.Symbol])]["digits"],
                "clid": msg[Field.ClOrdId],
            }
            if int(msg[Field.OrdType]) > 1:
                if price := msg[Field.Price]:
                    self.order_list[msg[Field.OrderID]]["price"] = float(price)
                else:
                    self.order_list[msg[Field.OrderID]]["price"] = float(msg[Field.StopPx])
            if name not in self.spot_request_list:
                self.spot_market_request(name)
            self.order_list_callback(self.order_list, self.spot_price_list)

    def process_logon(self, msg):
        if msg[Field.SenderSubID] == "QUOTE":
            logging.info("Quote logged on")
            self.ping_qworker_thread = threading.Thread(target=self.ping_qworker, args=[int(msg[Field.HeartBtInt])])
            self.ping_qworker_thread.start()
        elif msg[Field.SenderSubID] == "TRADE":
            logging.info("Trade logged on")
            self.ping_tworker_thread = threading.Thread(target=self.ping_tworker, args=[int(msg[Field.HeartBtInt])])
            self.ping_tworker_thread.start()

    def process_market_data(self, msg: Message):
        name = self.sec_id_table[int(msg[Field.Symbol])]["name"]
        digits = self.sec_id_table[int(msg[Field.Symbol])]["digits"]
        entries = msg.get_repeating_groups(Field.NoMDEntries, Field.MDEntryType)
        if not msg[Field.MDEntryID] and msg[Field.NoMDEntries] != "0":
            self.spot_price_list[name] = {}
            for e in entries:
                self.spot_price_list[name]["bid" if e[Field.MDEntryType] == "0" else "offer"] = float(e[Field.MDEntryPx])
            self.position_list_callback(self.position_list, self.spot_price_list)
            self.order_list_callback(self.order_list, self.spot_price_list)
            return
        self.market_data[name] = {}
        for e in entries:
            eid = e[Field.MDEntryID]
            self.market_data[name][eid] = {
                "type": int(e[Field.MDEntryType]),
                "price": float(e[Field.MDEntryPx]),
                "size": float(e[Field.MDEntrySize]),
            }
        # logging.debug(pformat(msg))
        self.market_callback(name, digits, self.market_data[name])

    def process_market_incr_data(self, msg: Message):
        name = self.sec_id_table[int(msg[Field.Symbol])]["name"]
        digits = self.sec_id_table[int(msg[Field.Symbol])]["digits"]
        entries = msg.get_repeating_groups(Field.NoMDEntries, Field.MDUpdateAction)
        for e in entries:
            if e[Field.MDUpdateAction] == "2":
                del self.market_data[name][e[Field.MDEntryID]]
            elif e[Field.MDUpdateAction] == "0":
                eid = e[Field.MDEntryID]
                self.market_data[name][eid] = {
                    "type": int(e[Field.MDEntryType]),
                    "price": float(e[Field.MDEntryPx]),
                    "size": float(e[Field.MDEntrySize]),
                }
        # logging.debug(pformat(msg))
        self.market_callback(name, digits, self.market_data[name])

    def process_sec_list(self, msg):
        sec_list = msg.get_repeating_groups(Field.NoRelatedSym, Field.Symbol)
        for symbol in sec_list:
            self.sec_id_table[int(symbol[Field.Symbol])] = {"name": symbol[Field.SymbolName], "digits": int(symbol[Field.SymbolDigits])}
            self.sec_name_table[symbol[Field.SymbolName]] = {"id": int(symbol[Field.Symbol]), "digits": int(symbol[Field.SymbolDigits])}
        if self.sec_list_callback is not None:
            self.sec_list_callback()
        self.position_request()
        self.order_request()

    def process_position_list(self, msg):
        if msg[Field.PosReqResult] == "2":
            return
        name = self.sec_id_table[int(msg[Field.Symbol])]["name"]
        self.position_list[msg[Field.PosMaintRptID]] = {
            "name": name,
            "long": float(msg[Field.LongQty]),
            "short": float(msg[Field.ShortQty]),
            "price": float(msg[Field.SettlPrice]),
            "digits": self.sec_id_table[int(msg[Field.Symbol])]["digits"]
        }
        if name not in self.spot_request_list:
            self.spot_market_request(name)
        base = name[-3:]
        if base != self.currency:
            pair = "%s%s" % (base, self.currency)
            conv_dir = 0
            if not self.sec_name_table.get(pair, None):
                pair = "%s%s" % (self.currency, base)
                conv_dir = 1
            self.position_list[msg[Field.PosMaintRptID]]["convert"] = pair
            self.position_list[msg[Field.PosMaintRptID]]["convert_dir"] = conv_dir
            if pair not in self.spot_request_list:
                self.spot_market_request(pair)
        self.position_list_callback(self.position_list, self.spot_price_list)

    def process_reject(self, msg):
        logging.error(msg[Field.Text])

    message_dispatch = {
        "0": process_ping,
        "1": process_test,
        "5": process_logout,
        "8": process_exec_report,
        "A": process_logon,
        "j": process_reject,
        "W": process_market_data,
        "X": process_market_incr_data,
        "y": process_sec_list,
        "AP": process_position_list,
    }

    def process_message(self, msg: Message):
        msg_type = msg[Field.MsgType]
        FIX.message_dispatch[msg_type](self, msg)

    def send_message(self, msg: Message):
        if msg[Field.TargetSubID] == SubID.QUOTE:
            self.qs.send(bytes(msg))
            logging.debug("\033[36mSEND >>> %s\033[0m" % msg)
        elif msg[Field.TargetSubID] == SubID.TRADE:
            self.ts.send(bytes(msg))
            logging.debug("\033[96mSEND >>> %s\033[0m" % msg)

    def qheartbeat(self, test_id: int = None):
        msg = FIX.Message(SubID.QUOTE, "0", self)
        if test_id:
            msg[Field.TestReqID] = test_id
        self.send_message(msg)

    def theartbeat(self, test_id: int = None):
        msg = FIX.Message(SubID.TRADE, "0", self)
        if test_id:
            msg[Field.TestReqID] = test_id
        self.send_message(msg)

    def test(self):
        msg = FIX.Message(SubID.QUOTE, "1", self)
        msg[Field.TestReqID] = self.qtest_seq
        self.qtest_seq += 1
        self.send_message(msg)

        msg = FIX.Message(SubID.TRADE, "1", self)
        msg[Field.TestReqID] = self.ttest_seq
        self.ttest_seq += 1
        self.send_message(msg)

    def logon(self):
        msg = FIX.Message(SubID.QUOTE, "A", self)
        msg[Field.EncryptMethod] = 0
        msg[Field.HeartBtInt] = 30
        msg[Field.Username] = self.login
        msg[Field.Password] = self.password
        self.send_message(msg)
        msg = FIX.Message(SubID.TRADE, "A", self)
        msg[Field.EncryptMethod] = 0
        msg[Field.HeartBtInt] = 30
        msg[Field.Username] = self.login
        msg[Field.Password] = self.password
        self.send_message(msg)

    def market_request(self, subid, symbol, callback):
        if symbol not in self.sec_name_table.keys():
            logging.error("Symbol %s not found!" % symbol)
            return

        if self.subscribed_symbol[subid] != -1:
            msg = FIX.Message(SubID.QUOTE, "V", self)
            msg[Field.MDReqID] = self.market_seq
            msg[Field.SubscriptionRequestType] = 2
            msg[Field.MarketDepth] = 0
            msg[Field.NoMDEntryTypes] = 2
            msg[Field.MDEntryType] = 0
            msg[Field.MDEntryType] = 1
            msg[Field.NoRelatedSym] = 1
            msg[Field.Symbol] = self.subscribed_symbol[subid]
            self.send_message(msg)
            self.market_seq += 1
        msg = FIX.Message(SubID.QUOTE, "V", self)
        msg[Field.MDReqID] = self.market_seq
        msg[Field.SubscriptionRequestType] = 1
        msg[Field.MarketDepth] = 0
        msg[Field.NoMDEntryTypes] = 2
        msg[Field.MDEntryType] = 0
        msg[Field.MDEntryType] = 1
        msg[Field.NoRelatedSym] = 1
        msg[Field.Symbol] = self.sec_name_table[symbol]["id"]
        self.subscribed_symbol[subid] = msg[Field.Symbol]
        self.market_callback = callback
        self.send_message(msg)
        self.market_seq += 1

    def spot_market_request(self, symbol):
        msg = FIX.Message(SubID.QUOTE, "V", self)
        msg[Field.MDReqID] = -1
        msg[Field.SubscriptionRequestType] = 1
        msg[Field.MarketDepth] = 1
        msg[Field.NoMDEntryTypes] = 2
        msg[Field.MDEntryType] = 0
        msg[Field.MDEntryType] = 1
        msg[Field.NoRelatedSym] = 1
        msg[Field.Symbol] = self.sec_name_table[symbol]["id"]
        self.spot_request_list.add(symbol)
        self.send_message(msg)

    def position_request(self):
        msg = FIX.Message(SubID.TRADE, "AN", self)
        msg[Field.PosReqID] = 1
        self.send_message(msg)

    def order_request(self):
        msg = FIX.Message(SubID.TRADE, "AF", self)
        msg[Field.MassStatusReqID] = 1
        msg[Field.MassStatusReqType] = 7
        self.send_message(msg)

    def sec_list(self, callback = None):
        msg = FIX.Message(SubID.QUOTE, "x", self)
        msg[Field.SecurityReqID] = 1
        msg[Field.SecurityListRequestType] = 0
        self.sec_list_callback = callback
        self.send_message(msg)

    def new_market_order(self, symbol, side: Side, size: float, pos_id = None):
        msg = FIX.Message(SubID.TRADE, "D", self)
        msg[Field.ClOrdId] = get_date()
        msg[Field.Symbol] = self.sec_name_table[symbol]["id"]
        msg[Field.Side] = side.value
        msg[Field.TransactTime] = get_date()
        msg[Field.OrderQty] = size
        msg[Field.OrdType] = OrderType.Market.value
        if pos_id:
            msg[Field.PosMaintRptID] = pos_id
        self.send_message(msg)

    def close_position(self, pos_id: str):
        msg = FIX.Message(SubID.TRADE, "D", self)
        msg[Field.ClOrdId] = get_date()
        msg[Field.Symbol] = self.sec_name_table[self.position_list[pos_id]["name"]]["id"]
        msg[Field.Side] = Side.Sell.value if self.position_list[pos_id]["long"] > 0 else Side.Buy.value
        msg[Field.TransactTime] = get_date()
        msg[Field.OrderQty] = self.position_list[pos_id]["long"] if msg[Field.Side] == Side.Sell else self.position_list[pos_id]["short"]
        msg[Field.PosMaintRptID] = pos_id
        msg[Field.OrdType] = OrderType.Market.value
        self.send_message(msg)

    def new_limit_order(self, symbol, side: Side, order_type: OrderType, size: float, price: float, pos_id = None):
        msg = FIX.Message(SubID.TRADE, "D", self)
        msg[Field.ClOrdId] = get_date()
        msg[Field.Symbol] = self.sec_name_table[symbol]["id"]
        msg[Field.Side] = side.value
        msg[Field.TransactTime] = get_date()
        msg[Field.OrderQty] = size
        msg[Field.OrdType] = order_type.value
        if order_type == OrderType.Limit:
            msg[Field.Price] = price
        elif order_type == OrderType.Stop:
            msg[Field.StopPx] = price
        if pos_id:
            msg[Field.PosMaintRptID] = pos_id
        self.send_message(msg)

    def cancel_order(self, clid: str):
        msg = FIX.Message(SubID.TRADE, "F", self)
        msg[Field.OrigClOrdID] = clid
        msg[Field.ClOrdId] = get_date()
        self.send_message(msg)