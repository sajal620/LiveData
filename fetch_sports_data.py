#!/usr/bin/env python3

import copy
import hashlib
import json
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ============================================================
# CONFIG
# ============================================================

SOURCE_URL = (
    "https://raw.githubusercontent.com/"
    "sm-monirulislam/Upcoming-and-Live-Sports-Data/"
    "refs/heads/main/Sports_data.json"
)

MANUAL_CONTROL_FILE = "manual_control.json"

BD_TIMEZONE = ZoneInfo("Asia/Dhaka")
EVENT_TIME_FORMAT = "%d/%m/%Y %I:%M:%S %p"


# ============================================================
# STATUS GROUPS
# (LIVE / ENDED / UPCOMING শনাক্তকরণের সম্পূর্ণ তালিকা)
# ============================================================

LIVE_SOURCE_STATUSES = {
    # ---------- Generic ----------
    "LIVE", "INPLAY", "IN-PLAY", "IN PLAY", "ONGOING",
    "STARTED", "PLAYING", "NOW PLAYING", "RUNNING",

    # ---------- Football / Soccer ----------
    "1H", "2H", "HT", "ET", "BT", "PEN", "PENALTIES",
    "BREAK", "HALF TIME", "HALFTIME", "FIRST HALF",
    "SECOND HALF", "EXTRA TIME", "PENALTY SHOOTOUT",
    "INT", "SUSP", "SUSPENDED", "PAUSED",

    # ---------- Basketball / American Football ----------
    "Q1", "Q2", "Q3", "Q4", "OT", "OT1", "OT2",
    "1Q", "2Q", "3Q", "4Q",

    # ---------- Tennis / Badminton / Volleyball ----------
    "SET1", "SET2", "SET3", "SET4", "SET5",
    "1ST SET", "2ND SET", "3RD SET", "4TH SET", "5TH SET",
    "1S", "2S", "3S", "4S", "5S",

    # ---------- Cricket / Baseball ----------
    "INN1", "INN2", "INNINGS", "1ST INN", "2ND INN",
    "INN", "1IN", "2IN",

    # ---------- Hockey / Others ----------
    "P1", "P2", "P3",
}

ENDED_SOURCE_STATUSES = {
    "ENDED", "END", "FT", "FINISHED", "FULL TIME", "FULLTIME",
    "AET", "AFTER EXTRA TIME", "AP", "AFTER PENALTIES",
    "COMPLETED", "COMPLETE", "ABANDONED", "ABAN",
    "CANCELLED", "CANCELED", "CANC", "POSTPONED", "POSTP",
    "DELAYED", "DEL", "AWD", "AWARDED", "WO", "WALKOVER",
}

UPCOMING_SOURCE_STATUSES = {
    "UPCOMING", "SCHEDULED", "NOT STARTED", "NS",
    "TBD", "PRE", "PRE-GAME", "PRE GAME",
}


# ============================================================
# FETCH MAIN API
# ============================================================

def fetch_data():
    req = Request(
        SOURCE_URL,
        headers={
            "User-Agent": "sports-data-fetcher/1.0"
        }
    )

    with urlopen(req, timeout=30) as response:
        return json.load(response)


# ============================================================
# FIND MATCHES
# ============================================================

def find_matches(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in (
            "matches",
            "events",
            "data",
            "sports"
        ):
            if isinstance(data.get(key), list):
                return data[key]

    return []


# ============================================================
# TEXT NORMALIZER
# ============================================================

def normalize_text(value):
    if value is None:
        return ""

    value = str(value).strip().lower()

    # Remove extra spaces
    value = re.sub(r"\s+", " ", value)

    # Normalize common Vs variations
    value = re.sub(r"\s+vs\.?\s+", " vs ", value)

    # Remove punctuation
    value = re.sub(r"[^a-z0-9\s]", "", value)

    # Remove duplicate spaces again
    value = re.sub(r"\s+", " ", value).strip()

    return value


# ============================================================
# AUTO EVENT ID
# ============================================================

def generate_event_id(match):
    """
    Main API তে id না থাকলেও এখানে automatically
    stable deterministic ID তৈরি হবে।

    Primary identity:
        Category
        teamA
        teamB
        eventName

    যদি এগুলো খুব generic/empty হয়,
    event_name + startTime fallback হিসেবে ব্যবহার হবে।
    """

    if not isinstance(match, dict):
        return "evt_unknown"

    category = normalize_text(
        match.get("Category", match.get("category", ""))
    )

    event_name = normalize_text(
        match.get("event_name", match.get("eventName", ""))
    )

    event_info = match.get("eventInfo", {})

    if not isinstance(event_info, dict):
        event_info = {}

    team_a = normalize_text(
        event_info.get("teamA", "")
    )

    team_b = normalize_text(
        event_info.get("teamB", "")
    )

    tournament_name = normalize_text(
        event_info.get("eventName", "")
    )

    start_time = normalize_text(
        event_info.get("startTime", "")
    )

    # --------------------------------------------------------
    # Primary identity
    # --------------------------------------------------------

    identity_parts = [
        category,
        team_a,
        team_b,
        tournament_name
    ]

    identity = "|".join(
        part for part in identity_parts
        if part
    )

    # --------------------------------------------------------
    # Fallback
    # --------------------------------------------------------

    if not identity:
        identity = "|".join(
            part for part in [
                category,
                event_name,
                start_time
            ]
            if part
        )

    if not identity:
        identity = "unknown_event"

    # --------------------------------------------------------
    # SHA256 short ID
    # --------------------------------------------------------

    hash_id = hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()[:12]

    return f"evt_{hash_id}"


# ============================================================
# ASSIGN IDS TO ALL EVENTS
# ============================================================

def assign_event_ids(matches):
    used_ids = set()
    generated_count = 0

    for index, match in enumerate(matches):
        if not isinstance(match, dict):
            continue

        existing_id = match.get("id")

        if existing_id:
            event_id = str(existing_id).strip()
        else:
            event_id = generate_event_id(match)
            generated_count += 1

        # ----------------------------------------------------
        # Duplicate protection
        # ----------------------------------------------------

        original_id = event_id
        counter = 2

        while event_id in used_ids:
            event_id = f"{original_id}_{counter}"
            counter += 1

        match["id"] = event_id
        used_ids.add(event_id)

    return generated_count


# ============================================================
# EVENT NAME
# ============================================================

def get_event_name(match):
    if not isinstance(match, dict):
        return "Unknown Event"

    return str(
        match.get(
            "event_name",
            match.get("eventName", "Unknown Event")
        )
    ).strip()


# ============================================================
# SOURCE STATUS
# ============================================================

def get_source_status(match):
    if not isinstance(match, dict):
        return ""

    return str(
        match.get(
            "status",
            match.get("match_status", "")
        )
    ).strip().upper()


# ============================================================
# START TIME PARSER
# ============================================================

def parse_event_start_time(match):
    if not isinstance(match, dict):
        return None

    event_info = match.get("eventInfo")

    if not isinstance(event_info, dict):
        return None

    start_time = event_info.get("startTime")

    if not start_time:
        return None

    try:
        parsed = datetime.strptime(
            str(start_time).strip(),
            EVENT_TIME_FORMAT
        )

        return parsed.replace(
            tzinfo=BD_TIMEZONE
        )

    except (ValueError, TypeError):
        return None


# ============================================================
# MANUAL CONTROL LOADER
# ============================================================

def load_manual_control():
    try:
        with open(
            MANUAL_CONTROL_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return {}

        return data

    except FileNotFoundError:
        print(
            f"[MANUAL] {MANUAL_CONTROL_FILE} not found. "
            "Using empty manual control."
        )
        return {}

    except json.JSONDecodeError as exc:
        print(
            f"[MANUAL] Invalid JSON in "
            f"{MANUAL_CONTROL_FILE}: {exc}"
        )
        return {}


# ============================================================
# STATUS OVERRIDE
# ============================================================

def apply_status_override(
    match,
    manual_control
):
    if not isinstance(match, dict):
        return False

    event_id = str(
        match.get("id", "")
    ).strip()

    if not event_id:
        return False

    overrides = manual_control.get(
        "status_override",
        {}
    )

    if not isinstance(overrides, dict):
        return False

    if event_id not in overrides:
        return False

    new_status = str(
        overrides[event_id]
    ).strip().upper()

    if not new_status:
        return False

    match["status"] = new_status

    print(
        f"[MANUAL STATUS] "
        f"{event_id} | "
        f"{get_event_name(match)} | "
        f"Status={new_status}"
    )

    return True


# ============================================================
# UPDATE MATCH STATUS
# ------------------------------------------------------------
# এখানেই মূল সংশোধন করা হয়েছে।
# HT, 1H, 2H, ET, Q1-Q4, SET1-5, INN1-2 ইত্যাদি সব লাইভ
# হিসেবে শনাক্ত হবে।
# ============================================================

def update_match_status(
    match,
    now_bd,
    manual_control
):
    if not isinstance(match, dict):
        return "UPCOMING"

    event_name = get_event_name(match)
    source_status = get_source_status(match)

    # --------------------------------------------------------
    # 1. Manual status override has highest priority
    # --------------------------------------------------------

    if apply_status_override(
        match,
        manual_control
    ):
        return match["status"]

    # --------------------------------------------------------
    # 2. Source says ENDED
    # --------------------------------------------------------

    if source_status in ENDED_SOURCE_STATUSES:
        match["status"] = "ENDED"

        print(
            f"[STATUS] {event_name} | "
            f"Source={source_status} | "
            f"Final=ENDED"
        )

        return "ENDED"

    # --------------------------------------------------------
    # 3. Source says LIVE
    # --------------------------------------------------------

    if source_status in LIVE_SOURCE_STATUSES:
        match["status"] = "LIVE"

        return "LIVE"

    # --------------------------------------------------------
    # 4. Source says UPCOMING
    # --------------------------------------------------------

    if source_status in UPCOMING_SOURCE_STATUSES:
        match["status"] = "UPCOMING"

        return "UPCOMING"

    # --------------------------------------------------------
    # 5. Unknown / empty source status
    #
    # IMPORTANT:
    # startTime দিয়ে LIVE/ENDED নির্ধারণ করা হবে না।
    #
    # অজানা স্ট্যাটাস এলে UPCOMING হিসেবে ধরা হবে,
    # এবং কনসোলে লগ প্রিন্ট হবে যাতে ভবিষ্যতে নতুন
    # স্ট্যাটাস যোগ করা যায়।
    # --------------------------------------------------------

    if source_status:
        print(
            f"[UNKNOWN STATUS] {event_name} | "
            f"Source={source_status} | "
            f"Defaulting to UPCOMING"
        )

    match["status"] = "UPCOMING"

    return "UPCOMING"


# ============================================================
# UPDATE ALL STATUSES
# ============================================================

def update_all_match_statuses(
    matches,
    now_bd,
    manual_control
):
    live_count = 0
    upcoming_count = 0
    ended_count = 0

    for match in matches:

        status = update_match_status(
            match,
            now_bd,
            manual_control
        )

        if status == "LIVE":
            live_count += 1

        elif status == "UPCOMING":
            upcoming_count += 1

        elif status == "ENDED":
            ended_count += 1

    return (
        live_count,
        upcoming_count,
        ended_count
    )


# ============================================================
# SORT EVENTS
# LIVE = FIRST
# UPCOMING = MIDDLE
# ENDED = LAST
# ============================================================

def sort_matches_by_status(matches):
    status_order = {
        "LIVE": 0,
        "UPCOMING": 1,
        "ENDED": 2
    }

    def sort_key(match):
        if not isinstance(match, dict):
            return 1

        status = str(
            match.get("status", "")
        ).strip().upper()

        return status_order.get(
            status,
            1
        )

    matches.sort(key=sort_key)

    return matches


# ============================================================
# REMOVE EVENTS
# ============================================================

def remove_manual_events(
    matches,
    manual_control
):
    remove_ids = manual_control.get(
        "remove_events",
        []
    )

    if not isinstance(remove_ids, list):
        return matches, 0

    remove_ids = {
        str(event_id).strip()
        for event_id in remove_ids
        if str(event_id).strip()
    }

    if not remove_ids:
        return matches, 0

    original_count = len(matches)

    matches = [
        match
        for match in matches
        if not (
            isinstance(match, dict)
            and str(
                match.get("id", "")
            ).strip() in remove_ids
        )
    ]

    removed_count = (
        original_count - len(matches)
    )

    if removed_count:
        print(
            f"[MANUAL] Events removed: "
            f"{removed_count}"
        )

    return matches, removed_count


# ============================================================
# REMOVE STREAMS
# ============================================================

def remove_manual_streams(
    matches,
    manual_control
):
    remove_streams = manual_control.get(
        "remove_streams",
        {}
    )

    if not isinstance(remove_streams, dict):
        return 0

    removed_count = 0

    for match in matches:

        if not isinstance(match, dict):
            continue

        event_id = str(
            match.get("id", "")
        ).strip()

        if event_id not in remove_streams:
            continue

        stream_names = remove_streams[event_id]

        if not isinstance(stream_names, list):
            continue

        stream_names = {
            str(name).strip().lower()
            for name in stream_names
            if str(name).strip()
        }

        streams = match.get(
            "streams",
            []
        )

        if not isinstance(streams, list):
            continue

        original_count = len(streams)

        match["streams"] = [
            stream
            for stream in streams
            if not (
                isinstance(stream, dict)
                and str(
                    stream.get(
                        "channel_name",
                        stream.get(
                            "channelName",
                            ""
                        )
                    )
                ).strip().lower()
                in stream_names
            )
        ]

        removed_count += (
            original_count -
            len(match["streams"])
        )

    if removed_count:
        print(
            f"[MANUAL] Streams removed: "
            f"{removed_count}"
        )

    return removed_count


# ============================================================
# ADD MANUAL STREAMS
# ============================================================

def add_manual_streams(
    matches,
    manual_control
):
    add_streams = manual_control.get(
        "add_streams",
        {}
    )

    if not isinstance(add_streams, dict):
        return 0

    added_count = 0

    for match in matches:

        if not isinstance(match, dict):
            continue

        event_id = str(
            match.get("id", "")
        ).strip()

        if event_id not in add_streams:
            continue

        new_streams = add_streams[event_id]

        if not isinstance(new_streams, list):
            continue

        streams = match.get(
            "streams",
            []
        )

        if not isinstance(streams, list):
            streams = []

        existing_names = {
            str(
                stream.get(
                    "channel_name",
                    stream.get(
                        "channelName",
                        ""
                    )
                )
            ).strip().lower()
            for stream in streams
            if isinstance(stream, dict)
        }

        for new_stream in new_streams:

            if not isinstance(
                new_stream,
                dict
            ):
                continue

            channel_name = str(
                new_stream.get(
                    "channel_name",
                    ""
                )
            ).strip()

            stream_url = str(
                new_stream.get(
                    "stream_url",
                    ""
                )
            ).strip()

            # Empty URL add করা হবে না
            if not channel_name:
                continue

            if not stream_url:
                print(
                    f"[MANUAL] Skipped empty URL: "
                    f"{channel_name}"
                )
                continue

            if (
                channel_name.lower()
                in existing_names
            ):
                print(
                    f"[MANUAL] Stream already exists: "
                    f"{channel_name}"
                )
                continue

            streams.append(
                copy.deepcopy(new_stream)
            )

            existing_names.add(
                channel_name.lower()
            )

            added_count += 1

            print(
                f"[MANUAL] Stream added | "
                f"{event_id} | "
                f"{channel_name}"
            )

        match["streams"] = streams

    return added_count


# ============================================================
# REMOVE EMPTY STREAM URLs
# ============================================================

def remove_empty_stream_urls(matches):
    removed_count = 0

    for match in matches:
        if not isinstance(match, dict):
            continue

        streams = match.get("streams", [])

        if not isinstance(streams, list):
            continue

        valid_streams = []

        for stream in streams:
            if not isinstance(stream, dict):
                continue

            stream_url = str(
                stream.get("stream_url", "")
            ).strip()

            # Empty URL হলে বাদ যাবে
            if not stream_url:
                removed_count += 1

                print(
                    f"[AUTO REMOVE] Empty stream URL | "
                    f"{get_event_name(match)} | "
                    f"{stream.get('channel_name', 'Unknown Stream')}"
                )

                continue

            valid_streams.append(stream)

        match["streams"] = valid_streams

    return removed_count


# ============================================================
# ADD MANUAL EVENTS
# ============================================================

def add_manual_events(
    matches,
    manual_control
):
    add_events = manual_control.get(
        "add_events",
        []
    )

    if not isinstance(add_events, list):
        return 0

    existing_ids = {
        str(
            match.get("id", "")
        ).strip()
        for match in matches
        if isinstance(match, dict)
    }

    added_count = 0

    for event in add_events:

        if not isinstance(event, dict):
            continue

        event = copy.deepcopy(event)

        event_id = str(
            event.get("id", "")
        ).strip()

        # ----------------------------------------------------
        # Manual event ID না দিলে auto generate
        # ----------------------------------------------------

        if not event_id:
            event_id = generate_event_id(
                event
            )

        original_id = event_id
        counter = 2

        while event_id in existing_ids:
            event_id = (
                f"{original_id}_manual_{counter}"
            )
            counter += 1

        event["id"] = event_id

        # ----------------------------------------------------
        # Streams ensure list
        # ----------------------------------------------------

        streams = event.get(
            "streams",
            []
        )

        if not isinstance(streams, list):
            event["streams"] = []

        matches.append(event)
        existing_ids.add(event_id)

        added_count += 1

        print(
            f"[MANUAL] Event added | "
            f"{event_id} | "
            f"{get_event_name(event)}"
        )

    return added_count


# ============================================================
# DRM KEY CONVERSION
# ============================================================

def convert_drm_keys(data):
    matches = find_matches(data)

    converted_count = 0

    for match in matches:

        if not isinstance(match, dict):
            continue

        streams = match.get(
            "streams",
            []
        )

        if not isinstance(streams, list):
            continue

        for stream in streams:

            if not isinstance(stream, dict):
                continue

            kid = stream.get("kid")
            key = stream.get("key")

            if (
                kid is not None
                and key is not None
            ):
                kid = str(kid).strip()
                key = str(key).strip()

                if kid and key:

                    stream["drm_key"] = (
                        f"{kid}:{key}"
                    )

                    stream.pop(
                        "kid",
                        None
                    )

                    stream.pop(
                        "key",
                        None
                    )

                    converted_count += 1

    return converted_count


# ============================================================
# MAIN
# ============================================================

def main():

    output_file = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "sports_data.json"
    )

    now_bd = datetime.now(
        BD_TIMEZONE
    )

    print("=" * 70)
    print("SPORTS DATA UPDATE START")
    print(
        "Bangladesh Time:",
        now_bd.strftime(
            "%d/%m/%Y %I:%M:%S %p"
        )
    )
    print("=" * 70)

    try:

        # ----------------------------------------------------
        # FETCH SOURCE
        # ----------------------------------------------------

        data = fetch_data()

        print(
            "Main API fetched successfully."
        )

        matches = find_matches(data)

        print(
            "Main API matches:",
            len(matches)
        )

        # ----------------------------------------------------
        # SOURCE STATUS SUMMARY (DEBUG)
        # সোর্সে কী কী স্ট্যাটাস আছে তা দেখতে
        # ----------------------------------------------------

        statuses = {}
        for m in matches:
            if isinstance(m, dict):
                s = get_source_status(m)
                statuses[s] = statuses.get(s, 0) + 1
        print("Source statuses:", statuses)

        # ----------------------------------------------------
        # AUTO GENERATE EVENT IDS
        # ----------------------------------------------------

        generated_ids = assign_event_ids(
            matches
        )

        print(
            "Auto generated IDs:",
            generated_ids
        )

        # ----------------------------------------------------
        # LOAD MANUAL CONTROL
        # ----------------------------------------------------

        manual_control = (
            load_manual_control()
        )

        # ----------------------------------------------------
        # REMOVE EVENTS
        # ----------------------------------------------------

        matches, removed_events = (
            remove_manual_events(
                matches,
                manual_control
            )
        )

        # ----------------------------------------------------
        # REMOVE STREAMS
        # ----------------------------------------------------

        removed_streams = (
            remove_manual_streams(
                matches,
                manual_control
            )
        )

        # ----------------------------------------------------
        # ADD STREAMS
        # ----------------------------------------------------

        added_streams = (
            add_manual_streams(
                matches,
                manual_control
            )
        )

        # ----------------------------------------------------
        # ADD NEW EVENTS
        # ----------------------------------------------------

        added_events = (
            add_manual_events(
                matches,
                manual_control
            )
        )

        # ----------------------------------------------------
        # REMOVE EMPTY STREAM URLs
        # ----------------------------------------------------

        empty_streams_removed = (
            remove_empty_stream_urls(matches)
        )

        # ----------------------------------------------------
        # Re-assign IDs after manual events
        # ----------------------------------------------------

        assign_event_ids(matches)

        # ----------------------------------------------------
        # UPDATE STATUS
        # ----------------------------------------------------

        (
            live_count,
            upcoming_count,
            ended_count
        ) = update_all_match_statuses(
            matches,
            now_bd,
            manual_control
        )

        # ----------------------------------------------------
        # SORT EVENTS
        # LIVE FIRST -> UPCOMING -> ENDED LAST
        # ----------------------------------------------------

        sort_matches_by_status(matches)

        # ----------------------------------------------------
        # DRM CONVERSION
        # ----------------------------------------------------

        if isinstance(data, dict):
            data["matches"] = matches

        drm_count = convert_drm_keys(
            data
        )

        # ----------------------------------------------------
        # FINAL TOP LEVEL DATA
        # ----------------------------------------------------

        if isinstance(data, dict):

            data["matches"] = matches

            data["total_matches"] = (
                len(matches)
            )

            data["live_match"] = (
                live_count
            )

            data["last_update_time"] = (
                now_bd.strftime(
                    "%I:%M:%S %p %d-%m-%Y"
                )
            )

        # ----------------------------------------------------
        # WRITE OUTPUT
        # ----------------------------------------------------

        with open(
            output_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

            f.write("\n")

        # ----------------------------------------------------
        # SUMMARY
        # ----------------------------------------------------

        print("=" * 70)
        print("FINAL SUMMARY")
        print("=" * 70)

        print(
            "Total matches:",
            len(matches)
        )

        print(
            "LIVE:",
            live_count
        )

        print(
            "UPCOMING:",
            upcoming_count
        )

        print(
            "ENDED:",
            ended_count
        )

        print(
            "Auto IDs:",
            generated_ids
        )

        print(
            "Events removed:",
            removed_events
        )

        print(
            "Streams removed:",
            removed_streams
        )

        print(
            "Streams added:",
            added_streams
        )

        print(
            "Events added:",
            added_events
        )

        print(
            "Empty streams removed:",
            empty_streams_removed
        )

        print(
            "DRM converted:",
            drm_count
        )

        print(
            "Output:",
            output_file
        )

        print("=" * 70)
        print(
            "SPORTS DATA UPDATE SUCCESS"
        )
        print("=" * 70)

        return 0

    except (
        HTTPError,
        URLError,
        TimeoutError,
        json.JSONDecodeError,
        OSError
    ) as exc:

        print(
            f"ERROR: {exc}",
            file=sys.stderr
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
