"""
Library computer terminal (comptwo.zil:679-1171).

The TERMINAL in LIBRARY-LOBBY shows a numeric menu tree of Residan
encyclopaedia entries.  Two entry points:

    handle_terminal(ctx)        TERMINAL-F   — examine / read / turn on / off
    library_type(caller, num)   LIBRARY-TYPE — the player TYPEs a number

Calling library_type (V-TYPE, verbs.zil:1630):
    V-TYPE only checks the room — ``<EQUAL? ,HERE ,LIBRARY-LOBBY>`` → LIBRARY-
    TYPE; elsewhere (other than MINI-BOOTH) it prints "Type on what???".  There
    is NO terminal-on (ONBIT) check anywhere: typing at a dark terminal still
    navigates the menus and prints the screen text, exactly as in the original.
    Use ``can_type_here(caller)`` for the room guard.  Pass ``num=None`` when
    PRSO isn't INTNUM (the player typed a word) to get NUMBERS-ONLY.

State: ONBIT/TOUCHBIT live in TERMINAL's db.obj_flags; the ZIL globals
MENU-LEVEL and SCREEN-TEXT live in gs.flags (SCREEN-TEXT stored as a key into
SCREENS, not the text itself).
"""
from __future__ import annotations

# --- text globals (comptwo.zil:1002-1171, verbs.zil:1625) ------------------

NUMBERS_ONLY = ("This keyboard only has numeric keys. You can type numbers on "
                "it, but not words.")

NO_MEANING = ("The terminal feeps, and a message briefly appears on the screen "
              "explaining that typing that character has no meaning at the "
              "moment.")

SCREEN_CLEARS = "The screen clears and a different menu appears:\n"

TEXT_APPEARS = "The screen clears and some text appears:\n"

MORE_INFO = ("\n\"Foor moor deetaald infoormaashun on xis tapik, konsult xe "
             "liibrereein foor xe aproopreeit spuulz. Tiip zeeroo tuu goo tuu "
             "aa hiiyur levul.\"")

LOW_END = ("\"Yuu hav reect xe loowist levul uv xe liibreree indeks. Pleez "
           "tiip zeeroo tuu goo tuu aa hiiyur levul. If yuu reekwiir asistins, "
           "kawl xe liibrereein.\"")

SCREENS = {
    "MAIN-MENU": ("    1. Histooree\n"
                  "    2. Kulcur\n"
                  "    3. Teknolojee\n"
                  "    4. Jeeografee\n"
                  "    5. Xe Prajekt\n"
                  "    6. Inturlajik Gaamz"),
    "HISTORY-MENU": ("    0. Maan Menyuu\n"
                     "    1. Raashul Orijinz\n"
                     "    2. Graat Hiiaatus\n"
                     "    3. Riiz uv xe Nuu Teknakrasee"),
    "CULTURE-MENU": ("    0. Maan Menyuu\n"
                     "    1. Lituracur\n"
                     "    2. Art\n"
                     "    3. Muusik"),
    "TECHNOLOGY-MENU": ("    0. Maan Menyuu\n"
                        "    1. Medisin\n"
                        "    2. Agrikultcur\n"
                        "    3. Tranzportaashun\n"
                        "    4. Roobotiks\n"
                        "    5. Planateree Sistumz"),
    "GEOGRAPHY-MENU": ("    0. Maan Menyuu\n"
                       "    1. Planit Landmasiz\n"
                       "    2. Undursee Reejunz\n"
                       "    3. Spaas Kolooneez"),
    # (sic) the ZIL string ends with a trailing "|".
    "PROJECT-MENU": ("    0. Maan Menyuu\n"
                     "    1. Orijinz uv xe Dizeez\n"
                     "    2. Xe Instalaashunz\n"
                     "    3. Prajekt Kuntrool\n"),
    "INTERLOGIC-MENU": ("    0. Maan Menyuu\n"
                        "    1. Zoork\n"
                        "    2. Dedliin and Witnis\n"
                        "    3. Starkros and Suspendid"),
    "11-TEXT": (
        "\"Xe aancint lejindz saa xat ships frum xe Sekund Yuunyun wuns fild ar "
        "skiis and wil wun daa kum agen. Madern siientists, huu wuns dismist "
        "suc lejindz and felt xat liif eevolvd heer on Resida, now feel xat ar "
        "planit wuz reelee setuld bii men uv xe Sekund Yuunyun.\""),
    "12-TEXT": (
        "\"Wexur oor not xe lejindz uv xe Sekund Yuunyun ar truu, arkeeoloojists "
        "ar surtin xat aa peereeid uv hii teknoolojikul and sooshul deevelupmint "
        "egzistid xowzindz uv yeerz agoo, but foor sum reezin sivilizaashun slid "
        "intuu aa dark aaj lasteeng senshureez.\""),
    "13-TEXT": (
        "\"Wixin xe last fiiv senshureez, xe riiz uv xe Nuu Teknakrasee haz "
        "reeturnd sivilizaashun tuu xe levul ataand beefoor xe Hiiaatus. Sooshul "
        "histooreeunz xink xat wen xe Dizeez struk, ar raas had aceevd aa levul "
        "uv sufistikaashun eekwal tuu xe pree-Hiiaatus.\""),
    "21-TEXT": (
        "\"Menee volyuumz on xe deevelupmint uv Residan lituracur ar on fiil in "
        "xe liibreree. Alsoo, kopeez uv awl graat wurks uv riiteeng, sum "
        "daateeng bak tuu xe mixikul daaz uv xe Sekund Yuunyun, ar lookaatid "
        "heer.\""),
    "22-TEXT": (
        "\"Histoorikul studeez and reeproodukshunz uv Residan art ar avaalibul "
        "heer foor awl xree maajur peereeids uv art deevelupmint: xe Primitiv "
        "peereeid, xe Renasans peereeid uv xe urlee poost-Hiiaatus, and xe "
        "moost reesint peereeid uv videeoo and laazur art.\""),
    "23-TEXT": (
        "\"Reekoordeengz uv awl impoortint kompoozishunz uv xe last fiiv hundrid "
        "yeerz ar lookaatid in xe liibrereez data banks.\""),
    "31-TEXT": (
        "\"Awl maajur dizeezuz hav bin kyuuribul foor oovur aa senshuree. Xe "
        "deevelupmint uv kriioojeniks now alowz dokturz tuu put paashints in "
        "staasis until aa kyuur iz fownd. Avurij Residan liif ekspektinsee iz "
        "now 147 revooluushunz.\""),
    "32-TEXT": (
        "\"Durt farmeeng iz awl but obsooleet, wix moost fuud kumeeng frum xe "
        "hiidrooponiks kompleksiz oor xe undurwatur aljee farmz.\""),
    "33-TEXT": (
        "\"Planateree travul iz noormulee priivit skuutur foor shoort hops, and "
        "aarbus foor longur trips.  Spaas travul haz reesintlee bin "
        "revooluushuniizd bii xe invenshun uv nuukleeur-fyuuld enjinz.\""),
    "34-TEXT": (
        "\"Untoold senshureez agoo, entiir teemz uv roobots wur reekwiird tuu "
        "purfoorm eevin xe simplist tasks...wun roobot wud handul viszuuwul "
        "funkshunz, wun roobot wud handul awditooree funkshunz, and soo foorx. "
        "Now, xanks tuu advansis in mineeatshurizaashun, xeez tasks kan bee "
        "purfoormd bii singul roobots, suc az xe multiipurpis B-19 seereez.\""),
    "35-TEXT": (
        "\"Xe priimeree Planateree Sistumz ar Kors Kuntrool (foor maantaaneeng "
        "an iideel kliimit), Deefens (foor destroieeng pootenshulee daanjuris "
        "meeteeoorz), and xe reesintlee adid Prajekt Kuntrool (foor monitureeng "
        "proogres uv Xe Prajekt).\""),
    "41-TEXT": (
        "\"Sins xe staabulizaashun uv xe oorbit uv Resida, preesiislee 47.79 "
        "pursent uv xe planits surfis iz land. Xe land iz diviidid intuu tuu "
        "priimeree landmasiz, Andoor and Fruulik, plus siks lesur landmasiz. Xe "
        "gloobul kapitul, Pilandoor, iz on xe eesturn koost uv Andoor.\""),
    "42-TEXT": (
        "\"Xe furst undursee habutats wur oopind in 2992, and tuudaa, neerlee "
        "tuu senshureez laatur, abowt 9 pursent uv Residaz popyuulaashun livz "
        "in wun uv xe twentee sprawleeng undursee siteez.\""),
    "43-TEXT": (
        "\"Alxoo setulmints hav bin establisht on Fristin, and on sevrul uv xe "
        "muunz uv xe gas jiiunt Blustin, xe vast majooritee uv of-woorldurz liv "
        "in xe spaas kolooneez establisht at Residaz troojin points.\""),
    "51-TEXT": (
        "\"Xe oorijin uv Xe Dizeez haz bin linkt tuu xe Sentur foor Advanst "
        "Kriioojenik Reesurc, wic wuz kondukteeng reesurc intuu waaz uv "
        "ekstendeeng xe Kriioojenik peereeid indefinitlee. Alxoo xis reesurc "
        "wuz aa sukses, sumhow Xe Dizeez wuz reeleest and beegan spredeeng.\""),
    "52-TEXT": (
        "\"Xe tuu kompleksiz wur establisht on xe twin peek platooz uv "
        "Kalamontee and Lawanda. Xeez lookaashunz wur coozin beekawz xaar hiit "
        "wud maak transpoortaashun and komyuunikaashunz eezeeur, and soo xat xe "
        "vast reeakturz and kriioojeniks caamburz kud bee kunstruktid in xe "
        "mowntinz beeloo.\""),
    "53-TEXT": (
        "\"Faaz Wun: xe kunstrukshun uv xe Kalamontee and Lawanda Kompleksiz. "
        "Faaz Tuu: mass kriioojenik freezeeng uv Residan popyuulaashun. Faaz "
        "Xree: siimultaaneeus monitureeng uv kriioojeniks wiil awtoomaatid "
        "reesurc iz konduktid bii inkrediblee soofistikaatid kumpyuuturiizd "
        "fasiliteez. Faaz Foor: reeviivul and inokyuulaashun uv xe "
        "popyuulaashun.\""),
    "61-TEXT": (
        "\"Xe Zoork triloojee, an adventshur klasik, taaks plaas in aa "
        "deeliitful but daanjuris undurgrownd seteeng.\""),
    "62-TEXT": (
        "\"Dedliin iz xe furst graat misturee uv xe kumpyuutur aaj, and Witnis "
        "iz its wurxee suksesur.\""),
    "63-TEXT": (
        "\"Starkros iz Infookamz miind-bendeeng siiens-fikshun adventshur. "
        "Suspendid iz aa kriioojenik siiens-fikshun niitmaar.\""),
}

# LIBRARY-TYPE's menu tree.  Main-menu key N (1-6) opens MENUS[N]; within menu
# N, keys 1..leaves open leaf "<N><key>-TEXT" (MENU-LEVEL N*10+key).
# `no_meaning_above` is the ZIL's <G? ,P-NUMBER x> bound.  (sic) the culture
# menu tests <G? 4>, so typing 4 there prints nothing at all.
MENUS = {
    1: ("HISTORY-MENU", 3, 3),
    2: ("CULTURE-MENU", 3, 4),
    3: ("TECHNOLOGY-MENU", 5, 5),
    4: ("GEOGRAPHY-MENU", 3, 3),
    5: ("PROJECT-MENU", 3, 3),
    6: ("INTERLOGIC-MENU", 3, 3),
}


# --- state helpers ---------------------------------------------------------

def _terminal():
    from world.action_handlers import _by_zil_id
    return _by_zil_id("TERMINAL")


def _menu_level(gs) -> int:
    return gs.flags.get("MENU-LEVEL", 0) if gs else 0


def _set_menu_level(gs, level: int) -> None:
    if gs:
        gs.flags["MENU-LEVEL"] = level


def _screen_text(gs) -> str:
    key = gs.flags.get("SCREEN-TEXT") if gs else None
    # SCREEN-TEXT is only ever read with ONBIT set, i.e. after LAMP-ON stored
    # MAIN-MENU; fall back to that if the flag is missing.
    return SCREENS.get(key or "MAIN-MENU", SCREENS["MAIN-MENU"])


def _set_screen(gs, key: str) -> str:
    if gs:
        gs.flags["SCREEN-TEXT"] = key
    return SCREENS[key]


def can_type_here(caller) -> bool:
    """V-TYPE's guard (verbs.zil:1656) — LIBRARY-TYPE runs only in LIBRARY-LOBBY."""
    loc = caller.location
    return bool(loc and loc.db.zil_id == "LIBRARY-LOBBY")


# --- TERMINAL-F ------------------------------------------------------------

def handle_terminal(ctx) -> bool:
    """TERMINAL-F (comptwo.zil:694) — examine / read the screen, switch on/off."""
    from world.action_handlers import _bit, _set_bit, _gs
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    gs = _gs()
    on = _bit(obj, "ONBIT")
    more = MORE_INFO if _menu_level(gs) > 9 else None
    if v == "V-EXAMINE":
        text = ("The computer terminal consists of a video display screen, a "
                "keyboard with ten keys numbered from zero through nine, and an "
                "on-off switch. ")
        if on:
            caller.msg(text + "The screen displays some writing:\n"
                       + _screen_text(gs))
            if more:
                caller.msg(more)
        else:
            caller.msg(text + "The screen is dark.")
        return True
    if v == "V-READ":
        if on:
            caller.msg(_screen_text(gs))
            if more:
                caller.msg(more)
        else:
            caller.msg("The screen is blank.")
        return True
    if v == "V-LAMP-ON":
        if on:
            caller.msg("It's already on.")
        else:
            _set_bit(obj, "ONBIT", True)
            _set_bit(obj, "TOUCHBIT", True)
            # Note: MENU-LEVEL is not reset here (only by LAMP-OFF), as in ZIL.
            text = _set_screen(gs, "MAIN-MENU")
            caller.msg("The screen gives off a green flash, and then some "
                       "writing appears on the screen:")
            caller.msg(text)
        return True
    if v == "V-LAMP-OFF":
        if on:
            _set_bit(obj, "ONBIT", False)
            _set_menu_level(gs, 0)
            caller.msg("The screen goes dark.")
        else:
            caller.msg("It isn't on!")
        return True
    return False


# --- LIBRARY-TYPE ----------------------------------------------------------

def _show_menu(caller, gs, level: int, key: str) -> None:
    _set_menu_level(gs, level)
    caller.msg(SCREEN_CLEARS)
    caller.msg(_set_screen(gs, key))


def library_type(caller, num: int | None) -> None:
    """LIBRARY-TYPE (comptwo.zil:734) — numeric navigation of the library index.

    No ONBIT check: the ZIL lets you type at a switched-off terminal.
    """
    from world.action_handlers import _gs
    if num is None:
        caller.msg(NUMBERS_ONLY)   # NUMBERS-ONLY (verbs.zil:1625)
        return
    gs = _gs()
    level = _menu_level(gs)

    if level == 0:
        if num == 0 or num > 6:
            caller.msg(NO_MEANING)
        elif num in MENUS:
            _show_menu(caller, gs, num, MENUS[num][0])
        return

    if level in MENUS:
        _key, leaves, no_meaning_above = MENUS[level]
        if num == 0:
            _show_menu(caller, gs, 0, "MAIN-MENU")
        elif 1 <= num <= leaves:
            leaf = level * 10 + num
            _set_menu_level(gs, leaf)
            caller.msg(TEXT_APPEARS)
            caller.msg(_set_screen(gs, f"{leaf}-TEXT"))
            caller.msg(MORE_INFO)
            if leaf == 61:
                _floyd_loves_zork(caller)
        elif num > no_meaning_above:
            caller.msg(NO_MEANING)
        return

    # Leaf screens (MENU-LEVEL 11-69): 0 goes back up, anything else bottoms out.
    parent = level // 10
    if 10 < level < 70 and parent in MENUS:
        if num == 0:
            _show_menu(caller, gs, parent, MENUS[parent][0])
        else:
            caller.msg(LOW_END)


def _floyd_loves_zork(caller) -> None:
    """<IN? ,FLOYD ,HERE> aside on the Zork entry (comptwo.zil:926); sets FLOYD-SPOKE."""
    from world.action_handlers import _by_zil_id, _floyd_npc
    floyd = _by_zil_id("FLOYD")
    if not (floyd and floyd.location is caller.location):
        return
    fnpc = _floyd_npc()
    if fnpc:
        fnpc.spoke = True
    caller.msg("Floyd, peering over your shoulder, says \"Oh, I love that game! "
               "Solved every problem, except couldn't figure out how to get "
               "into white house.\"")
