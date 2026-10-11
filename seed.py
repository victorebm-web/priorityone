"""Generate a deterministic mock dataset for PriorityOne.

All names, numbers and links are invented. The dataset is anchored to the current week
(override with PRIORITYONE_TODAY=YYYY-MM-DD); with the same anchor it is byte-for-byte identical.

Run: python seed.py
"""
import math
import random
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List

from db import Base, SessionLocal, current_week_start, engine
from models import (Account, AccountPaymentMethod, CallNote, CapSetting, Contact, SignalEvent, SourceSync, User,
                    WeeklyMetric)
from signals import (PEER_ADOPTION_THRESHOLD, SEVERITY_ORDER, detect_metric_signals, detect_state_signals, peer_method_gaps,
                     seasonal_index)

SEED = 42
N_WEEKS = 26
DETECTION_REPLAY_WEEKS = 12  # replay detection over this many past Mondays to build event history
GONG_LAG_DAYS = 12  # newest Gong call is this old, so Gong shows as a stale source

VP = "Helena Strand"
MANAGERS = ["Ingrid Vos", "Marcus Bell"]
AMS = [("Sofia Marquez", 0), ("Daniel Okafor", 0), ("Lena Fischer", 0),
       ("Tom Whitaker", 1), ("Priya Raman", 1), ("Jonas Berg", 1)]

ACCOUNT_NAMES = {
    "ecommerce": ["Nordvik Outfitters", "Lumen & Lark", "Cobalt Crate", "Fernhill Home", "Pebblewave",
                  "Tidewell Apparel", "Quillstone Books", "Marigold Market", "Arcturus Gear", "Velvetine Beauty"],
    "marketplace": ["Stallbridge", "Craftloom", "Rentaroo", "Bazaarly", "Gigforge",
                    "Tradewind Exchange", "Loopsy", "Handpicked Hub", "Swapnest", "Vendora"],
    "travel": ["Skyhaven Air", "Wayfarer Rail", "Halcyon Hotels", "Orbitra Trips", "Coastline Ferries",
               "Nomadique", "Peakline Tours", "Bluegate Stays", "Jetwise", "Sunpath Holidays"],
    "subscription": ["Streamhollow", "Fitpulse", "Meal Atlas", "Readwell", "Cloudnest Software",
                     "Tunebox", "Gardenly", "Learnhive", "Petpal Club", "Brewcrate"],
    "retail": ["Hartley & Finch", "Oakmere Department Stores", "Brightline Pharmacy", "Urban Kettle",
               "Northfield Electronics", "Mosswood Garden Centres", "Kestrel Sports", "Linden Toys",
               "Cascade Optics", "Pemberton Shoes"],
    "food delivery": ["Snackrun", "Forkfly", "Dishdash Express", "Nibblr", "Bowlrider",
                      "Spoonwise", "Platepost", "Grubwagon", "Munchmile", "Hotbox Delivery"],
}

FIRST = ["Anna", "Ben", "Chloe", "David", "Elena", "Felix", "Grace", "Hugo", "Isla", "Jakob", "Kara", "Leo",
         "Maya", "Noah", "Olivia", "Pedro", "Rosa", "Sam", "Tara", "Viktor", "Wen", "Yusuf", "Zoe", "Aiko",
         "Mateo", "Nina", "Omar", "Freya", "Ravi", "Hana"]
LAST = ["Andersen", "Brooks", "Castillo", "Dubois", "Eriksen", "Fontaine", "Greco", "Hansen", "Ito", "Jansen",
        "Kowalski", "Laurent", "Moreau", "Nakamura", "Ortega", "Petrov", "Quinn", "Rossi", "Silva", "Tanaka",
        "Usman", "Varga", "Weber", "Yilmaz", "Zhou", "Bakker", "Costa", "Novak", "Park", "Lindqvist"]
CONTACT_TITLES = ["Head of Payments", "VP Finance", "Director of E-commerce", "Payments Lead", "CFO",
                  "Head of Checkout", "Treasury Manager"]

STANDARD_METHODS = {
    "ecommerce": ["card", "apple_pay", "google_pay", "paypal"],
    "marketplace": ["card", "apple_pay", "google_pay", "paypal"],
    "travel": ["card", "amex", "apple_pay", "paypal"],
    "subscription": ["card", "apple_pay", "sepa_dd", "account_updater"],
    "retail": ["card", "apple_pay", "tap_to_pay"],
    "food delivery": ["card", "apple_pay", "paypal"],
}
# A method that most peers in the vertical have adopted recently. One account per vertical lacks it.
TRENDING_METHOD = {"ecommerce": "klarna", "travel": "alipay", "food delivery": "google_pay", "subscription": "paypal"}
EXTRA_METHODS = ["ideal", "wechat_pay", "pix", "klarna", "alipay"]

SEGMENT_ARR = {"Enterprise": (2_000_000, 15_000_000), "Mid-Market": (300_000, 2_000_000), "SMB": (40_000, 300_000)}
TAKE_RATE = 0.0025  # ARR / annual processing volume, used to size weekly volumes plausibly

CROSS_SELL_OPPS = ["Interest in Adyen Issuing for supplier payouts",
                   "Interest in Adyen Capital for working-capital financing",
                   "Interest in Terminal API for an in-store pop-up pilot"]


def name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def sf_id(rng: random.Random) -> str:
    return "0016" + "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz0123456789") for _ in range(11))


def gong_link(rng: random.Random) -> str:
    return f"https://app.gong.io/call?id={rng.randrange(10**18, 10**19)}"


def build_plan(rng: random.Random, accounts: List[Account]) -> Dict[str, List[int]]:
    """Choose which accounts carry each planted pattern. Some overlap is deliberate."""
    by_seg = defaultdict(list)
    for a in accounts:
        by_seg[a.segment].append(a.id)
    ent = by_seg["Enterprise"][:]
    rng.shuffle(ent)
    others = [a.id for a in accounts if a.id not in ent[:2]]
    rng.shuffle(others)
    # Positions 0 and 4 are Enterprise so the strongest overlaps land on big accounts.
    pool = [ent[0]] + others[:3] + [ent[1]] + others[3:]

    plan = {
        "volume_drop": pool[0:4],
        "auth_rate_drop": pool[4:7],
        "chargeback_spike": pool[7:9],
        "renewal_60d": [pool[0], pool[4]] + pool[9:12],    # vol drop + renewal; auth drop + renewal
        "sev1_open": [pool[5]] + pool[12:14],              # auth drop + sev-1 (incident story)
        "qbr_overdue": [pool[1]] + pool[14:19],
        "contact_changed": [pool[2], pool[19]],            # vol drop + champion leaving
        "volume_growth": pool[20:23],
        "failed_payout": pool[23:25],
        "cross_sell": pool[25:28],
    }
    used = set(i for ids in plan.values() for i in ids)
    vertical_of = {a.id: a.vertical for a in accounts}
    gap = []
    for vertical in TRENDING_METHOD:
        gap.append(next(i for i in pool[28:] if vertical_of[i] == vertical and i not in used))
    plan["payment_method_gap"] = gap
    return plan


def seed(verbose: bool = True) -> None:
    rng = random.Random(SEED)
    anchor = current_week_start()  # Monday of this week
    weeks = [anchor - timedelta(weeks=N_WEEKS - i) for i in range(N_WEEKS)]  # completed weeks, oldest first

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = SessionLocal()

    # --- Users ---------------------------------------------------------------------------
    vp_id = len(MANAGERS) + len(AMS) + 1
    managers = [User(id=i + 1, name=n, role="manager", manager_id=vp_id) for i, n in enumerate(MANAGERS)]
    ams = [User(id=len(MANAGERS) + i + 1, name=n, role="am", manager_id=managers[m].id)
           for i, (n, m) in enumerate(AMS)]
    s.add_all(managers + ams + [User(id=vp_id, name=VP, role="vp")])

    # --- Accounts ------------------------------------------------------------------------
    segments = ["Enterprise"] * 15 + ["Mid-Market"] * 25 + ["SMB"] * 20
    rng.shuffle(segments)
    owners = [am.id for am in ams for _ in range(10)]
    rng.shuffle(owners)
    accounts: List[Account] = []
    idx = 0
    for vertical, names in ACCOUNT_NAMES.items():
        for nm in names:
            seg = segments[idx]
            lo, hi = SEGMENT_ARR[seg]
            arr = round(rng.uniform(lo, hi), -3)
            accounts.append(Account(
                id=idx + 1, name=nm, vertical=vertical, segment=seg,
                region=rng.choice(["NA", "EMEA", "EMEA", "APAC"]), arr_usd=arr,
                customer_since=anchor - timedelta(days=rng.randint(400, 3300)),
                owner_id=owners[idx], csm_name=name(rng),
                renewal_date=anchor + timedelta(days=rng.randint(75, 700)),
                last_qbr_date=anchor - timedelta(days=rng.randint(15, 150)),
                primary_contact_name=name(rng), salesforce_id=sf_id(rng),
            ))
            idx += 1
    plan = build_plan(rng, accounts)
    acc = {a.id: a for a in accounts}

    for aid, days in zip(plan["renewal_60d"], [12, 25, 38, 47, 58]):
        acc[aid].renewal_date = anchor + timedelta(days=days)
    for aid, days in zip(plan["qbr_overdue"], [186, 195, 204, 215, 226, 234]):
        acc[aid].last_qbr_date = anchor - timedelta(days=days)
    for aid, days in zip(plan["contact_changed"], [6, 19]):
        a = acc[aid]
        a.previous_contact_name = a.primary_contact_name
        while a.primary_contact_name == a.previous_contact_name:
            a.primary_contact_name = name(rng)
        a.contact_changed_on = anchor - timedelta(days=days)
    extras = plant_extras(accounts, plan, anchor)
    plan.update(extras)
    s.add_all(accounts)

    # --- Payment methods -----------------------------------------------------------------
    methods: Dict[int, Dict[str, date]] = defaultdict(dict)
    for a in accounts:
        for m in STANDARD_METHODS[a.vertical]:
            methods[a.id][m] = a.customer_since + timedelta(days=rng.randint(0, 60))
        for m in EXTRA_METHODS:
            if m not in methods[a.id] and m != TRENDING_METHOD.get(a.vertical) and rng.random() < 0.12:
                methods[a.id][m] = a.customer_since + timedelta(days=rng.randint(30, 300))
    # Trending method: peers adopt it so the 60% threshold is crossed within the last few weeks.
    for vertical, method in TRENDING_METHOD.items():
        laggard = next(i for i in plan["payment_method_gap"] if acc[i].vertical == vertical)
        peers = [a.id for a in accounts if a.vertical == vertical and a.id != laggard]
        rng.shuffle(peers)
        ages = ([rng.randint(400, 900) for _ in range(3)] + [rng.randint(60, 100) for _ in range(2)]
                + [rng.randint(10, 35)] + [rng.randint(0, 9) for _ in range(len(peers) - 6)])
        for pid, age in zip(peers, sorted(ages, reverse=True)):
            methods[pid][method] = anchor - timedelta(days=age)
        methods[laggard].pop(method, None)
    for aid, ms in methods.items():
        for m, d in ms.items():
            s.add(AccountPaymentMethod(account_id=aid, method=m, enabled_on=d))

    # --- Weekly metrics ------------------------------------------------------------------
    metrics: Dict[int, List[WeeklyMetric]] = {}
    last = N_WEEKS - 1
    vol_drop = dict(zip(plan["volume_drop"], [0.38, 0.33, 0.28, 0.25]))
    vol_growth = dict(zip(plan["volume_growth"], [0.40, 0.31, 0.24]))
    auth_drop = dict(zip(plan["auth_rate_drop"], [9.0, 7.5, 6.2]))
    cb_spike = dict(zip(plan["chargeback_spike"], [(0.0105, 0.0185), (0.0095, 0.0162)]))
    sev1_weeks = dict(zip(plan["sev1_open"], [{24, 25}, {25}, {23, 24, 25}]))
    payout_spike = dict(zip(plan["failed_payout"], [12, 7]))
    # Past dips that recovered on their own or after action: these give the outcome tracking data.
    recov_vol = {aid: {N_WEEKS - 8: 0.25, N_WEEKS - 7: 0.30} for aid in plan["recovered_volume"]}
    recov_auth = {aid: {N_WEEKS - 7: 4.0, N_WEEKS - 6: 7.5} for aid in plan["recovered_auth"]}
    for a in accounts:
        base_vol = a.arr_usd / TAKE_RATE / 52
        slope = rng.uniform(-0.002, 0.003)
        auth_base = rng.uniform(0.86, 0.945)
        cb_base = rng.uniform(0.004, 0.007) if a.vertical == "travel" else rng.uniform(0.0015, 0.005)
        tickets = rng.randint(0, 3)
        rows = []
        for i, wk in enumerate(weeks):
            vol = base_vol * (1 + slope * i) * rng.gauss(1, 0.02) * seasonal_index(a.vertical, wk + timedelta(days=3))
            auth = auth_base + rng.gauss(0, 0.004)
            cb = cb_base * rng.gauss(1, 0.08)
            payouts = 1 if rng.random() < (0.3 if a.vertical == "marketplace" else 0.12) else 0
            tickets = max(0, min(8, tickets + rng.choice([-1, 0, 0, 1])))
            sev1 = 0
            back = last - i  # 0 = most recent completed week
            if a.id in vol_drop and back < 3:
                vol *= 1 - vol_drop[a.id] * (3 - back) / 3
            if a.id in vol_growth and back < 3:
                vol *= 1 + vol_growth[a.id] * (3 - back) / 3
            if a.id in auth_drop and back < 2:
                auth -= auth_drop[a.id] / 100 * (0.45 if back == 1 else 1.0)
            if a.id in cb_spike and back < 2:
                cb = cb_spike[a.id][1 - back]
            if a.id in sev1_weeks and i in sev1_weeks[a.id]:
                sev1 = 1
                tickets += 2
            if a.id in payout_spike and back < 2:
                payouts = payout_spike[a.id] if back == 0 else 3
            vol *= 1 - recov_vol.get(a.id, {}).get(i, 0)
            auth -= recov_auth.get(a.id, {}).get(i, 0) / 100
            rows.append(WeeklyMetric(
                account_id=a.id, week_start=wk, processing_volume_usd=round(vol, 2),
                auth_rate=round(min(0.98, max(0.70, auth)), 4),
                chargeback_rate=round(min(0.02, max(0.001, cb)), 5),
                failed_payout_count=payouts, support_tickets_open=tickets, support_sev1_open=sev1,
            ))
        metrics[a.id] = rows
        s.add_all(rows)

    # --- The long tail: 20 more accounts per AM, mostly quiet --------------------------
    tail, tail_rows = build_long_tail(accounts, ams, methods, metrics, weeks, anchor)
    fresh = plant_fresh_problems(tail, ams, metrics, anchor)
    s.add_all(tail)
    s.add_all(tail_rows)
    everyone = accounts + tail

    # --- Gong call notes -----------------------------------------------------------------
    notes = build_call_notes(rng, accounts, plan, anchor, {u.id: u.name for u in ams})
    plan["sentiment_decline"] = assign_sentiment(notes, accounts, plan, anchor)
    s.add_all(build_contacts(everyone, notes, anchor, {u.id: u.name for u in ams}))
    s.add_all(notes)
    notes_by_acc = defaultdict(list)
    for n in notes:
        notes_by_acc[n.account_id].append(n)
    for lst in notes_by_acc.values():
        lst.sort(key=lambda n: n.call_date)

    # --- Source sync log ----------------------------------------------------------------
    # Daily syncs for every source over the metrics window. The Gong connector "broke"
    # GONG_LAG_DAYS ago, so Gong is stale for the current week (but fine for earlier weeks).
    for source in ("payments_db", "salesforce", "gong", "support", "slack"):
        last_day = anchor - timedelta(days=GONG_LAG_DAYS) if source == "gong" else anchor
        d = weeks[0]
        while d <= last_day:
            s.add(SourceSync(source_system=source, synced_on=d, rows_synced=rng.randint(40, 400)))
            d += timedelta(days=1)
    # The NPS survey tool syncs weekly, on Fridays.
    d = weeks[0] + timedelta(days=4)
    while d <= anchor:
        s.add(SourceSync(source_system="survey", synced_on=d, rows_synced=rng.randint(5, 40)))
        d += timedelta(days=7)

    # --- Signal events: replay detection over past Mondays -------------------------------
    for pattern, aid in fresh:  # only now: call notes and sentiment read the plan and must not see these
        plan[pattern].append(aid)
    events = replay_detection(everyone, metrics, notes_by_acc, methods, anchor)
    s.add_all(events)
    s.add(CapSetting(user_id=None, mode="fixed", cap=7))  # the default list size; editable in Settings
    s.commit()

    if verbose:
        report(s, plan, anchor)
    s.close()


def plant_extras(accounts: List[Account], plan: Dict[str, List[int]], anchor: date) -> Dict[str, List[int]]:
    """Patterns added after the first version. A separate random stream keeps every earlier
    demo story byte-for-byte the same."""
    r = random.Random(SEED + 7)
    story = {i for ids in plan.values() for i in ids}
    free = [a for a in accounts if a.id not in story]
    r.shuffle(free)
    by_name = {a.name: a for a in accounts}

    def take(n, pred=lambda a: True):
        picked = [a for a in free if pred(a)][:n]
        for a in picked:
            free.remove(a)
        return picked

    # Baseline relationship data for everyone, healthy by default.
    for a in accounts:
        a.relationship_updated_on = anchor - timedelta(days=r.randint(2, 20))
        a.contacts_engaged_90d = r.randint(2, 6) if a.segment != "SMB" else r.randint(1, 3)
        if a.segment == "Enterprise":
            a.exec_sponsor_last_contact = anchor - timedelta(days=r.randint(20, 150))
        if r.random() < 0.7:
            a.nps_score = r.randint(7, 10)
            a.nps_date = anchor - timedelta(days=r.randint(20, 170))

    first_year = take(6)
    for a in first_year:
        a.customer_since = anchor - timedelta(days=r.randint(200, 330))
    detractors = [by_name["Nibblr"]] + take(2)          # a renewal plus a detractor: revenue at risk
    for a, (score, days) in zip(detractors, [(5, 9), (3, 16), (6, 30)]):
        a.nps_score, a.nps_date = score, anchor - timedelta(days=days)
    exec_gap = take(2, lambda a: a.segment == "Enterprise")
    for a, days in zip(exec_gap, [205, 228]):
        a.exec_sponsor_last_contact = anchor - timedelta(days=days)
    single = take(3, lambda a: a.segment in ("Enterprise", "Mid-Market"))
    for a in single:
        a.contacts_engaged_90d = 1
    recovered_volume = take(4, lambda a: a.vertical != "travel")
    recovered_auth = take(2)
    return {
        "first_year": [a.id for a in first_year], "nps_detractor": [a.id for a in detractors],
        "exec_sponsor_gap": [a.id for a in exec_gap], "single_threaded": [a.id for a in single],
        "recovered_volume": [a.id for a in recovered_volume], "recovered_auth": [a.id for a in recovered_auth],
    }


TAIL_PER_AM = 20
TAIL_SEGMENTS = ["Enterprise"] * 2 + ["Mid-Market"] * 7 + ["SMB"] * 11   # per AM: the long tail is small accounts
TAIL_PREFIX = ["Alder", "Brookfield", "Cinderleaf", "Driftwood", "Emberly", "Fablewick", "Glenmoor", "Harborlight",
               "Ivorybay", "Juniper", "Kitefield", "Larkspur", "Mapleton", "Northwind", "Opaline", "Pinecrest",
               "Quarrystone", "Riverbend", "Sablewood", "Thistledown", "Umberly", "Valemont", "Willowmere",
               "Yarrowfield", "Zephyrine", "Ashgrove", "Bellhaven", "Copperleaf", "Dovetail", "Elmstead"]
TAIL_SUFFIX = {
    "ecommerce": ["Goods", "Supply", "Studio", "Collective"], "marketplace": ["Market", "Exchange", "Bazaar", "Hub"],
    "travel": ["Travel", "Journeys", "Stays", "Getaways"], "subscription": ["Box", "Club", "Plus", "Cloud"],
    "retail": ["Stores", "Outfitters", "& Co", "Retail"], "food delivery": ["Eats", "Kitchen", "Delivery", "Bites"],
}


def build_long_tail(accounts: List[Account], ams: List[User], methods, metrics, weeks, anchor: date):
    """The rest of each AM's book: 20 more accounts each, mostly SMB and mid-market, mostly healthy.

    Their own random stream, generated after the original 60, so every planted story is unchanged. They are
    built to stay quiet under detection (steady volume, normal auth and chargebacks, healthy relationship
    data), except that some have an older QBR and no recent call, which is ordinary housekeeping.
    Peer adoption of the trending payment method is copied from the original peers, so benchmarks hold.
    """
    r = random.Random(SEED + 21)
    taken = {a.name for a in accounts}
    verticals = list(ACCOUNT_NAMES)
    peers_by_vertical = defaultdict(list)
    for a in accounts:
        peers_by_vertical[a.vertical].append(a.id)
    out: List[Account] = []
    extra: list = []  # their payment methods and weekly metrics
    next_id = max(a.id for a in accounts) + 1
    for am in ams:
        segs = TAIL_SEGMENTS[:]
        r.shuffle(segs)
        for k, seg in enumerate(segs):
            vertical = verticals[(next_id + k) % len(verticals)]
            nm = next(x for x in iter(lambda: f"{r.choice(TAIL_PREFIX)} {r.choice(TAIL_SUFFIX[vertical])}", None) if x not in taken)
            taken.add(nm)
            lo, hi = SEGMENT_ARR[seg]
            a = Account(
                id=next_id, name=nm, vertical=vertical, segment=seg, region=r.choice(["NA", "EMEA", "EMEA", "APAC"]),
                arr_usd=round(r.uniform(lo, min(hi, lo * 4)), -3), customer_since=anchor - timedelta(days=r.randint(420, 3000)),
                owner_id=am.id, csm_name=name(r), renewal_date=anchor + timedelta(days=r.randint(95, 700)),
                last_qbr_date=anchor - timedelta(days=r.randint(15, 85) if r.random() < 0.85 else r.randint(95, 170)),
                primary_contact_name=name(r), salesforce_id=sf_id(r),
                relationship_updated_on=anchor - timedelta(days=r.randint(2, 20)),
                contacts_engaged_90d=r.randint(2, 6) if seg != "SMB" else r.randint(1, 3),
                exec_sponsor_last_contact=anchor - timedelta(days=r.randint(20, 150)) if seg == "Enterprise" else None,
            )
            if r.random() < 0.7:
                a.nps_score, a.nps_date = r.randint(7, 10), anchor - timedelta(days=r.randint(20, 170))
            next_id += 1
            out.append(a)

            # Payment methods: the vertical's standard set, plus the trending one dated like an existing peer's.
            methods[a.id] = {m: a.customer_since + timedelta(days=r.randint(0, 60)) for m in STANDARD_METHODS[vertical]}
            trend = TRENDING_METHOD.get(vertical)
            if trend:
                # Copy a peer's adoption date, but never later than the day peer adoption crossed the
                # threshold, so a tail account is never itself flagged as missing the method.
                dates = sorted(methods[p][trend] for p in peers_by_vertical[vertical] if trend in methods[p])
                crossed = dates[max(math.ceil(PEER_ADOPTION_THRESHOLD * len(peers_by_vertical[vertical]) - 1e-9), 1) - 1]
                methods[a.id][trend] = min(r.choice(dates), crossed - timedelta(days=1))
            for m, d in methods[a.id].items():
                extra.append(AccountPaymentMethod(account_id=a.id, method=m, enabled_on=d))

            # Weekly metrics: steady, with the vertical's seasonality and ordinary noise.
            base_vol = a.arr_usd / TAKE_RATE / 52
            slope, auth_base = r.uniform(-0.0015, 0.002), r.uniform(0.87, 0.94)
            cb_base = r.uniform(0.004, 0.006) if vertical == "travel" else r.uniform(0.0015, 0.004)
            tickets, rows = r.randint(0, 2), []
            for i, wk in enumerate(weeks):
                tickets = max(0, min(6, tickets + r.choice([-1, 0, 0, 1])))
                rows.append(WeeklyMetric(
                    account_id=a.id, week_start=wk,
                    processing_volume_usd=round(base_vol * (1 + slope * i) * r.gauss(1, 0.02)
                                                * seasonal_index(vertical, wk + timedelta(days=3)), 2),
                    auth_rate=round(min(0.98, max(0.70, auth_base + r.gauss(0, 0.004))), 4),
                    chargeback_rate=round(min(0.02, max(0.001, cb_base * r.gauss(1, 0.08))), 5),
                    failed_payout_count=0, support_tickets_open=tickets, support_sev1_open=0,
                ))
            metrics[a.id] = rows
            extra.extend(rows)
    return out, extra


# Seven fresh problems in the long tail, all new in the latest completed week (so earlier weeks and their
# scripted stories don't move). Four land with Daniel, the demo AM, so his book shows 7 needing attention.
FRESH_PROBLEMS = [  # (AM, segment wanted, pattern)
    ("Daniel Okafor", "Enterprise", "volume_drop"), ("Daniel Okafor", "Mid-Market", "chargeback_spike"),
    ("Jonas Berg", "Mid-Market", "failed_payout"), ("Daniel Okafor", "Enterprise", "sev1_open"),
    ("Daniel Okafor", "SMB", "failed_payout"),  # smaller: ranks just below the list, so it shows as "flagged"
    ("Priya Raman", "Enterprise", "volume_drop"), ("Priya Raman", "Mid-Market", "nps_detractor"),
]


def plant_fresh_problems(tail: List[Account], ams: List[User], metrics, anchor: date) -> List[tuple]:
    """Give seven long-tail accounts a real problem in the last completed week. Returns (pattern, account_id)
    pairs for the caller to add to the plan, so the planted-vs-detected check covers them."""
    by_name = {u.name: u.id for u in ams}
    used, out = set(), []
    for am, seg, pattern in FRESH_PROBLEMS:
        a = next(t for t in tail if t.owner_id == by_name[am] and t.segment == seg and t.id not in used)
        used.add(a.id)
        last = metrics[a.id][-1]
        if pattern == "volume_drop":
            last.processing_volume_usd = round(last.processing_volume_usd * 0.64, 2)   # about 36% down: high
        elif pattern == "chargeback_spike":
            last.chargeback_rate = 0.0171                                              # above 1.5%: high
        elif pattern == "failed_payout":
            last.failed_payout_count = 11 if seg != "SMB" else 6                       # 10+ high; 5-9 medium
        elif pattern == "sev1_open":
            last.support_sev1_open, last.support_tickets_open = 1, last.support_tickets_open + 2
        elif pattern == "nps_detractor":
            a.nps_score, a.nps_date = 3, anchor - timedelta(days=4)                    # 0-3: high
        out.append((pattern, a.id))
    return out


CONTACT_ROLE_TITLES = {
    "champion": ["Head of Payments", "Payments Lead", "Director of E-commerce", "Head of Checkout"],
    "decision_maker": ["VP Payments", "Chief Digital Officer", "VP E-commerce"],
    "exec_sponsor": ["CFO", "Chief Operating Officer", "CEO", "CTO"],
    "finance": ["VP Finance", "Treasury Manager", "Finance Director"],
    "technical": ["Engineering Manager, Payments", "Lead Developer, Checkout", "Head of Platform"],
    "risk": ["Fraud & Risk Manager", "Head of Risk"],
    "procurement": ["Procurement Manager", "Strategic Sourcing Lead"],
    "operations": ["Head of Operations", "Operations Manager"],
}
SEGMENT_CONTACT_ROLES = {
    "Enterprise": ["exec_sponsor", "decision_maker", "finance", "technical", "risk", "procurement"],
    "Mid-Market": ["finance", "technical", "decision_maker"],
    "SMB": ["operations"],
}


def build_contacts(accounts: List[Account], notes: List[CallNote], anchor: date, am_names: Dict[int, str]) -> List[Contact]:
    """Salesforce contacts per account, consistent with the account's relationship fields, plus
    Gong attendee lists that name them. Its own random stream, so earlier demo stories don't move."""
    r = random.Random(SEED + 11)
    notes_by_acc = defaultdict(list)
    for n in notes:
        notes_by_acc[n.account_id].append(n)
    out: List[Contact] = []
    next_id = 1

    def person(a, taken, role, primary=False, last=None, left=None):
        nm = next(x for x in iter(lambda: name(r), None) if x not in taken)
        taken.add(nm)
        return add(a, nm, role, primary, last, left)

    def add(a, nm, role, primary=False, last=None, left=None):
        nonlocal next_id
        domain = "".join(ch for ch in a.name.lower() if ch.isalnum()) + ".example"
        c = Contact(id=next_id, account_id=a.id, name=nm, title=r.choice(CONTACT_ROLE_TITLES[role]), role=role,
                    email=f"{nm.split()[0].lower()}.{nm.split()[-1].lower()}@{domain}",
                    salesforce_id="003" + "".join(r.choice("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz0123456789") for _ in range(12)),
                    is_primary=primary, last_activity_on=last, left_on=left)
        next_id += 1
        out.append(c)
        return c

    for a in accounts:
        taken = {a.primary_contact_name, a.previous_contact_name, a.csm_name, am_names[a.owner_id]}
        primary = add(a, a.primary_contact_name, "champion" if a.segment != "SMB" else r.choice(["champion", "operations"]),
                      primary=True, last=a.last_qbr_date)
        if a.previous_contact_name:
            add(a, a.previous_contact_name, "champion", last=a.contact_changed_on, left=a.contact_changed_on)
        roles = list(SEGMENT_CONTACT_ROLES[a.segment])
        if a.segment == "Mid-Market" and r.random() < 0.5:
            roles.pop()  # not every mid-market account has a separate decision maker on file
        if a.segment == "SMB" and r.random() < 0.5:
            roles = ["finance"]
        others = []
        for role in roles:
            last = a.exec_sponsor_last_contact if role == "exec_sponsor" else None
            others.append(person(a, taken, role, last=last))
        # Who has engaged in the last 90 days: the primary, plus enough others to match the CRM count.
        engaged = [c for c in others if c.role != "exec_sponsor"]
        r.shuffle(engaged)
        engaged = engaged[:max(0, (a.contacts_engaged_90d or 1) - 1)]
        for c in others:
            if c.role == "exec_sponsor":
                continue
            c.last_activity_on = (anchor - timedelta(days=r.randint(8, 80)) if c in engaged
                                  else (anchor - timedelta(days=r.randint(100, 320)) if r.random() < 0.7 else None))
        # Gong attendees: the main contact at the time, plus engaged colleagues on recent calls.
        titles = {c.name: c.title for c in out if c.account_id == a.id}
        for n in notes_by_acc.get(a.id, []):
            main = a.previous_contact_name if (a.previous_contact_name and n.attendees.startswith(a.previous_contact_name)) else primary.name
            people = [main]
            recent = (anchor - n.call_date).days <= 90
            for c in (engaged if recent else [c for c in others if c.role != "exec_sponsor"]):
                if r.random() < (0.6 if recent else 0.25):
                    people.append(c.name)
            n.attendees = ", ".join(f"{p} ({titles[p]})" for p in people) + f", {am_names[a.owner_id]} (AM), {a.csm_name} (CSM)"
    return out


def assign_sentiment(notes: List[CallNote], accounts, plan, anchor) -> List[int]:
    """Gong call sentiment. Calls that raised risks are cooler; three accounts have a sharp decline."""
    r = random.Random(SEED + 11)
    for n in notes:
        n.sentiment = round(r.uniform(-0.05, 0.3) if n.extracted_risks else r.uniform(0.2, 0.7), 1)
    by_acc = defaultdict(list)
    for n in notes:
        by_acc[n.account_id].append(n)
    names = {a.id: a.name for a in accounts}
    recent = anchor - timedelta(days=50)
    candidates = [aid for aid, ns in sorted(by_acc.items())
                  if len(ns) >= 2 and max(n.call_date for n in ns) >= recent]
    # Tunebox (the routing test) plus two others with recent calls.
    picks = [aid for aid in candidates if names[aid] == "Tunebox"]
    picks += [aid for aid in candidates if aid not in picks and aid not in plan["nps_detractor"]][:3 - len(picks)]
    for aid, last in zip(picks, [-0.5, -0.3, -0.2]):
        ns = sorted(by_acc[aid], key=lambda n: n.call_date)
        ns[-2].sentiment, ns[-1].sentiment = 0.5, last
    return picks


def build_call_notes(rng, accounts, plan, anchor, am_names) -> List[CallNote]:
    newest = anchor - timedelta(days=GONG_LAG_DAYS)
    story = defaultdict(list)
    for key in ("volume_drop", "auth_rate_drop", "renewal_60d", "chargeback_spike", "sev1_open",
                "contact_changed", "volume_growth", "cross_sell"):
        for aid in plan[key]:
            story[aid].append(key)
    with_notes = set(story)
    rest = [a.id for a in accounts if a.id not in with_notes]
    rng.shuffle(rest)
    with_notes.update(rest[:30 - len(with_notes)])

    acc = {a.id: a for a in accounts}
    cross = dict(zip(plan["cross_sell"], CROSS_SELL_OPPS))
    out = []
    for aid in sorted(with_notes):
        a = acc[aid]
        n_notes = rng.randint(1, 3)
        dates = sorted(newest - timedelta(days=rng.randint(40, 190)) for _ in range(n_notes - 1))
        # The latest call is recent for accounts with a story, older for the rest.
        dates.append(newest - timedelta(days=rng.randint(0, 25) if story[aid] else rng.randint(20, 120)))
        for k, d in enumerate(dates):
            latest = k == len(dates) - 1
            contact = a.previous_contact_name if (a.previous_contact_name and not latest) else a.primary_contact_name
            summary, risks, opps = note_text(rng, a, contact, story[aid] if latest else [], cross.get(aid) if latest else None)
            out.append(CallNote(
                account_id=aid, call_date=d,
                attendees=f"{contact} ({rng.choice(CONTACT_TITLES)}), {am_names[a.owner_id]} (AM), {a.csm_name} (CSM)",
                source_system="gong", source_ref=gong_link(rng),
                summary=summary, extracted_risks=risks, extracted_opportunities=opps,
            ))
    return out


def note_text(rng, a: Account, contact: str, story: List[str], cross_opp):
    region = {"NA": "North America", "EMEA": "Europe", "APAC": "Asia-Pacific"}[a.region]
    sentences = [rng.choice([
        f"Monthly check-in with {contact} and {a.csm_name}.",
        f"Call with {contact} to review last quarter's payments performance.",
        f"Working session with {contact} on their checkout roadmap.",
    ])]
    risks, opps = [], []
    if "volume_drop" in story:
        sentences.append(f"{contact.split()[0]} mentioned they have started routing part of their {region} traffic to a second acquirer as a 'resilience test'.")
        risks.append("Routing volume to a second acquirer")
    if "auth_rate_drop" in story:
        sentences.append("They flagged more issuer declines since moving their checkout to a new 3DS2 flow.")
        risks.append("3DS2 migration hurting conversion")
    if "renewal_60d" in story:
        sentences.append("Procurement has joined the account and is asking for a pricing review ahead of the renewal.")
        risks.append("Pricing pressure at renewal")
    if "chargeback_spike" in story:
        sentences.append("They are seeing more friendly-fraud disputes on a new promotion and asked about tightening risk rules.")
        risks.append("Rising disputes on promotion")
        opps.append("RevenueProtect rule tuning")
    if "sev1_open" in story:
        sentences.append("They escalated intermittent webhook delays that are holding up order confirmations.")
        risks.append("Webhook reliability")
    if "contact_changed" in story and a.previous_contact_name:
        sentences.append(f"{a.previous_contact_name} confirmed they are leaving; {a.primary_contact_name} will own payments going forward.")
        risks.append("Champion leaving")
    if "volume_growth" in story:
        sentences.append("They launch in two new markets next month and expect volumes to step up.")
        opps.append("Expansion into new markets")
    if cross_opp:
        product = cross_opp.replace("Interest in ", "")
        sentences.append(f"{contact.split()[0]} asked several questions about {product}.")
        opps.append(cross_opp)
    if len(sentences) < 3:
        sentences.append(rng.choice([
            "Volumes are tracking in line with plan and the team is happy with settlement times.",
            f"They walked through plans for further growth in {region} over the next two quarters.",
            "Finance raised a question about interchange++ reporting, which we resolved on the call.",
            "Integration is stable and they are on a recent API version.",
        ]))
        if rng.random() < 0.4:
            opps.append(rng.choice(["Local payment methods in new markets", "Network tokens to lift recurring auth rates"]))
    sentences.append(rng.choice([
        f"Next step: {a.csm_name} to send a follow-up summary and book the next check-in.",
        "Agreed to reconvene in four weeks.",
        "Action: share a benchmark of peer auth rates before the next call.",
    ]))
    return " ".join(sentences[:5]), risks, opps


def replay_detection(accounts, metrics, notes_by_acc, methods, anchor) -> List[SignalEvent]:
    """Run detection as of each of the last N Mondays and keep an event log.

    New condition -> new event. Same condition with higher severity -> old event resolved, new one opened.
    Condition gone -> event resolved.
    """
    events: List[SignalEvent] = []
    by_vertical = defaultdict(list)
    for a in accounts:
        by_vertical[a.vertical].append(a.id)

    for a in accounts:
        open_: Dict[tuple, SignalEvent] = {}
        for k in range(DETECTION_REPLAY_WEEKS - 1, -1, -1):
            as_of = anchor - timedelta(weeks=k)
            done = [m for m in metrics[a.id] if m.week_start + timedelta(days=7) <= as_of]
            peers = [{m: d for m, d in methods[p].items()} for p in by_vertical[a.vertical] if p != a.id]
            gaps = peer_method_gaps(methods[a.id], peers, as_of)
            found = detect_metric_signals(a.id, done, as_of, a.vertical) + detect_state_signals(
                a, done, notes_by_acc.get(a.id, []), gaps, as_of)

            seen = set()
            for det in found:
                key = (det.signal_type, det.source_ref if det.signal_type in ("cross_sell_fit", "payment_method_gap") else "")
                seen.add(key)
                prev = open_.get(key)
                if prev and SEVERITY_ORDER[det.severity] <= SEVERITY_ORDER[prev.severity]:
                    continue
                if prev:
                    prev.resolved_on = min(as_of, det.onset)
                    prev.resolution = "superseded"
                ev = SignalEvent(
                    account_id=a.id, signal_type=det.signal_type, detected_on=det.onset, severity=det.severity,
                    value_now=det.value_now, value_before=det.value_before, source_system=det.source_system,
                    source_ref=det.source_ref, summary=det.summary, detail=det.detail,
                )
                open_[key] = ev
                events.append(ev)
            for key in list(open_):
                if key not in seen:
                    gone = open_.pop(key)
                    gone.resolved_on = as_of
                    gone.resolution = "cleared"
    return events


def report(s, plan, anchor) -> None:
    """Print planted patterns vs what detection found active this week."""
    active = s.query(SignalEvent).filter(SignalEvent.resolved_on.is_(None)).all()
    found = defaultdict(set)
    for e in active:
        if (anchor - e.detected_on).days <= 60:
            found[e.signal_type].add(e.account_id)
    checks = [("volume_drop", "volume_drop"), ("auth_rate_drop", "auth_rate_drop"),
              ("chargeback_spike", "chargeback_spike"), ("renewal_60d", "renewal_approaching"),
              ("sev1_open", "sev1_open"), ("qbr_overdue", "qbr_overdue"),
              ("payment_method_gap", "payment_method_gap"), ("contact_changed", "contact_departed"),
              ("volume_growth", "volume_growth"), ("failed_payout", "failed_payout"),
              ("cross_sell", "cross_sell_fit"), ("nps_detractor", "nps_detractor"),
              ("sentiment_decline", "sentiment_decline"), ("exec_sponsor_gap", "exec_sponsor_gap"),
              ("single_threaded", "single_threaded")]
    print(f"Seeded PriorityOne data anchored to week of {anchor}.")
    print(f"{'pattern':22} planted  detected  match")
    for planted_key, sig in checks:
        p, f = set(plan[planted_key]), found[sig]
        print(f"{planted_key:22} {len(p):7}  {len(f):8}  {'yes' if p == f else 'NO  extra=' + str(sorted(f - p)) + ' missing=' + str(sorted(p - f))}")
    print(f"no_contact_90d (emergent) accounts: {len(found['no_contact_90d'])}")
    cleared = {e.account_id for e in s.query(SignalEvent).filter(SignalEvent.resolution == "cleared",
                                                                 SignalEvent.signal_type.in_(["volume_drop", "auth_rate_drop"]))}
    planted = set(plan["recovered_volume"]) | set(plan["recovered_auth"])
    print(f"recovered dips (cleared in history): {len(planted & cleared)} of {len(planted)} planted")
    print(f"first-year customers (tenure factor): {len(plan['first_year'])}")
    false_alerts = seasonal_false_alerts(s, anchor)
    print(f"seasonality: {false_alerts} travel volume-drop alerts avoided this week by the seasonal adjustment")


def seasonal_false_alerts(s, anchor) -> int:
    """How many travel accounts would raise a volume drop this week without the seasonal adjustment."""
    from models import WeeklyMetric as WM
    n = 0
    for a in s.query(Account).filter(Account.vertical == "travel"):
        rows = s.query(WM).filter(WM.account_id == a.id).order_by(WM.week_start).all()
        raw = {d.signal_type for d in detect_metric_signals(a.id, rows, anchor, a.vertical, seasonal=False)}
        adj = {d.signal_type for d in detect_metric_signals(a.id, rows, anchor, a.vertical)}
        n += "volume_drop" in raw and "volume_drop" not in adj
    return n


if __name__ == "__main__":
    seed()
