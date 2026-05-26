import json, re, pathlib
from datetime import datetime
from typing import List, Dict
from bisect import bisect

"""
    Parse KakaoTalk chat-history .txt files exported in either
    the English or Korean locale.

    Public methods
    --------------
    parse_chat_file(path|str)
        Accumulates messages from one file (call multiple times for multi-file exports).

    exportJson(out_path|str)
        Writes the aggregated messages list to a JSON file.

    Attributes
    ----------
    messages : List[Dict]
        Every element = {   "timestamp"     : "YYYY-MM-DD HH:MM",
                            "sender"        : str,
                            "text"          : str,
                            "time_of_day"   : str,
                            "season"        : str }
        Chronologically sorted when exportJson() is called.
"""

# ---------------------------------------------------------------------
#  Regex library
# ---------------------------------------------------------------------

#  Day divider lines
DATE_DIV_KO = re.compile(r"^(\d{4})년 (\d{1,2})월 (\d{1,2})일")  # 2021년 6월 21일 월요일
DATE_DIV_EN = re.compile(                                      # Thursday, July 16, 2020
    r"^[A-Za-z]+,\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})"
)

#  Message lines
MSG_KO = re.compile(  # 2021-06-21 오전 2:04, 재희씨 : text
    r"^(\d{4}-\d{2}-\d{2})\s+(오전|오후)\s+(\d{1,2}):(\d{2}),\s+([^:]+?)\s+:\s+(.*)$"
)
MSG_EN_ISO = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}):(\d{2}),\s+([^:]+?)\s+:\s+(.*)$"
)

MONTH_ABBR = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
MONTH_FULL = "January February March April May June July August September October November December".split()
MONTH_LOOKUP = {m: i + 1 for i, m in enumerate(MONTH_ABBR)}
MONTH_LOOKUP.update({m: i + 1 for i, m in enumerate(MONTH_FULL)})

MSG_EN_MONTH = re.compile(  # Jul 16, 2020 22:23, Name : text
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),\s+(\d{4})\s+(\d{2}):(\d{2}),\s+([^:]+?)\s+:\s+(.*)$",
    re.IGNORECASE,
)

class ChatParser:
    """
    Parse KakaoTalk .txt exports in English or Korean locale.
    After parsing call `exportJson(path)` to write the messages list.
    """

    def __init__(self):
        self.messages: List[Dict] = []
    
    # -----------------------------------------------------------------
    #  Public API
    # -----------------------------------------------------------------

    def parse_chat_file(self, file_path: str | pathlib.Path) -> None:
        """
        Read one .txt file exported from KakaoTalk
        Call multiple times for multi-file folders.
        """

        p = pathlib.Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(p)
        
        with p.open(encoding="utf-8") as f:
            lines = f.readlines()
        
        # skip first 2 non-blank header
        non_blank = 0
        for idx, line in enumerate(lines):
            if line.strip():
                non_blank += 1
                if non_blank == 2:
                    start_idx = idx + 1
                    break
        else:
            start_idx = len(lines)

        current_date = None

        for raw in (ln.rstrip("\n") for ln in lines[start_idx:]):
            if not raw.strip():
                continue

            # day dividers
            if m := DATE_DIV_KO.match(raw):
                y, mo, d = m.groups()
                current_date = f"{y}-{int(mo):02d}-{int(d):02d}"
                continue
            if m := DATE_DIV_EN.match(raw):
                month_name, day, year = m.groups()
                month = MONTH_LOOKUP[month_name]
                current_date = f"{year}-{month:02d}-{int(day):02d}"
                continue

            # message handling
            # Kor
            if m := MSG_KO.match(raw):
                date, ampm, hour, minute, sender, text = m.groups()
                hour = int(hour)
                if ampm == "오후" and hour != 12:
                    hour += 12
                if ampm == "오전" and hour == 12:
                    hour = 0
                ts = f"{date} {hour:02d}:{minute}"
                self._add(ts, sender, text)
                continue

            # ISO
            if m := MSG_EN_ISO.match(raw):
                date, hour, minute, sender, text = m.groups()
                ts = f"{date} {hour}:{minute}"
                self._add(ts,sender,text)
                continue

            # Eng
            if m := MSG_EN_MONTH.match(raw):
                mon, day, year, hour, minute, sender, text = m.groups()
                month = MONTH_LOOKUP[mon.title()]
                ts = f"{year}-{month:02d}-{int(day):02d} {hour}:{minute}"
                self._add(ts, sender, text)
                continue

            # fallback: line missing date
            if current_date and ", " in raw:
                guess = f"{current_date} {raw}"
                if n := MSG_EN_ISO.match(guess):
                    _, hour, minute, sender, text = n.groups()
                    ts = f"{current_date} {hour}:{minute}"
                    self._add(ts, sender, text)
        

        # lineCount = 0

        # with open(file_path, 'r', encoding='utf-8') as f:
        #     for line in f:
        #         lineCount += 1
        #         if lineCount <= 2:
        #             continue
        #         line = line.strip()
        #         print(line)

        #         if line == "":
        #             continue

        #         if "," in line and len(line.split(",")) == 3:
        #             try:
        #                 current_date = datetime.strptime(line, "%A, %B %d, %Y").date()
        #             except ValueError:
        #                 pass
                    
        #         try:
        #             parts = line.split(",", 2)
        #             timestamp_str = f"{parts[0].strip()}, {parts[1].strip()}"
        #             dt = datetime.strptime(timestamp_str, "%b %d, %Y %H:%M")
        #             timestamp = dt.strftime("%Y-%m-%d %H:%M")

        #             sender_msg = parts[2].strip()
        #             sender, text = sender_msg.split(":", 1)

        #             timeOfDay, season = self.get_time_context(dt)

        #             self.messages.append({
        #                 "timestamp": timestamp,
        #                 "sender": sender.strip(),
        #                 "text": text.strip(),
        #                 "time_of_day": timeOfDay,
        #                 "season": season,
        #             })
        #         except (ValueError, IndexError) as e:
        #             print("Failed to parse: ",line)
        #             print(f"Reaseon: {e}")
        #             continue

    def exportJson(self, out_path: str | pathlib.Path) -> None:
        print("messages: ",len(self.messages))
        self.messages.sort(key=lambda m: m["timestamp"])
        out = pathlib.Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open('w',encoding='utf-8') as f:
            json.dump(self.messages, f, ensure_ascii=False, indent=2)
        print(f"✅ Exported {len(self.messages)} messages to {out_path}")

    def get_time_context(self, dt: datetime):
        # time of day
        hour_edges = [4, 11, 16, 21]
        tod_labels = ("night", "morning", "afternoon", "evening", "night")
        tod = tod_labels[bisect(hour_edges, dt.hour)]
        
        month_edges = [2, 5, 8, 11]
        season_labels = ("winter", "spring", "summer", "fall", "winter")
        season = season_labels[bisect(month_edges, dt.month)]

        # hour = dt.hour
        # month = dt.month
        
        # if 5 <= hour < 12 : tod = "morning"
        # elif 12 <= hour < 17: tod = "afternoon"
        # elif 17 <= hour < 22: tod = "evening"
        # else: tod = "night"

        # if month in [12, 1, 2]: season = "winter"
        # elif month in [3, 4, 5]: season = "spring"
        # elif month in [6, 7, 8]: season = "summer"
        # else: season = "fall"

        return tod, season
    
    def _add(self, ts: str, sender: str, text: str) -> None:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M")
        tod, season = self.get_time_context(dt)
        self.messages.append({
            "timestamp": ts,
             "sender": sender.strip(),
             "content": text.strip(),
             "time_of_day": tod,
             "season": season
        })
