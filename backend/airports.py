import json
import math
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

POLISH_AIRPORTS: Dict[str, Dict[str, Any]] = {
    "WAW": {"name": "Warszawa Chopin", "city": "Warszawa", "lat": 52.1657, "lon": 20.9671},
    "WMI": {"name": "Warszawa Modlin", "city": "Warszawa Modlin", "lat": 52.4511, "lon": 20.6517},
    "KRK": {"name": "Kraków Balice", "city": "Kraków", "lat": 50.0777, "lon": 19.7848},
    "KTW": {"name": "Katowice Pyrzowice", "city": "Katowice", "lat": 50.4743, "lon": 19.0800},
    "GDN": {"name": "Gdańsk Rębiechowo", "city": "Gdańsk", "lat": 54.3776, "lon": 18.4662},
    "WRO": {"name": "Wrocław Strachowice", "city": "Wrocław", "lat": 51.1027, "lon": 16.8858},
    "POZ": {"name": "Poznań Ławica", "city": "Poznań", "lat": 52.4210, "lon": 16.8263},
    "RZE": {"name": "Rzeszów Jasionka", "city": "Rzeszów", "lat": 50.1100, "lon": 22.0190},
    "LCJ": {"name": "Łódź Lublinek", "city": "Łódź", "lat": 51.7219, "lon": 19.3981},
    "SZZ": {"name": "Szczecin Goleniów", "city": "Szczecin", "lat": 53.5847, "lon": 14.9022},
    "BZG": {"name": "Bydgoszcz Szwederowo", "city": "Bydgoszcz", "lat": 53.0968, "lon": 17.9777},
    "LUZ": {"name": "Lublin Świdnik", "city": "Lublin", "lat": 51.2403, "lon": 22.7136},
}

# Every single country separated individually
WORLD_PRESETS: List[Dict[str, Any]] = [
    {"id": "WORLD", "label": "Cały Świat", "group": "Ogólne", "codes": "WORLD", "suggested_price": 2800, "suggested_stops": 2, "suggested_travel_h": 26, "suggested_layover_h": 6},
    {"id": "EUROPE", "label": "Cała Europa", "group": "Ogólne", "codes": "", "suggested_price": 350, "suggested_stops": 0, "suggested_travel_h": 6, "suggested_layover_h": 4},
    {"id": "PL", "label": "Polska (Loty krajowe)", "group": "Ogólne", "codes": "WAW,KRK,GDN,WRO,POZ,KTW,RZE,SZZ,BZG,LUZ,SZY,IEG", "suggested_price": 280, "suggested_stops": 0, "suggested_travel_h": 3, "suggested_layover_h": 2},
    # Południe Europy i Wyspy
    {"id": "IT", "label": "Włochy", "group": "Europa Południowa", "codes": "BGY,MXP,CIA,FCO,BRI,NAP,CTA,PMO,BLQ,PSA,VCE,TSF", "suggested_price": 420, "suggested_stops": 0, "suggested_travel_h": 5, "suggested_layover_h": 3},
    {"id": "ES", "label": "Hiszpania", "group": "Europa Południowa", "codes": "BCN,GRO,MAD,AGP,ALC,VLC,PMI,TFS,LPA,FUE,IBZ,SVQ", "suggested_price": 520, "suggested_stops": 1, "suggested_travel_h": 9, "suggested_layover_h": 4},
    {"id": "GR", "label": "Grecja", "group": "Europa Południowa", "codes": "ATH,SKG,CFU,CHQ,HER,RHO,ZTH,KGS,JTR", "suggested_price": 520, "suggested_stops": 1, "suggested_travel_h": 9, "suggested_layover_h": 4},
    {"id": "HR", "label": "Chorwacja", "group": "Europa Południowa", "codes": "ZAD,SPU,DBV,ZAG,PUY", "suggested_price": 420, "suggested_stops": 1, "suggested_travel_h": 7, "suggested_layover_h": 4},
    {"id": "CY", "label": "Cypr", "group": "Europa Południowa", "codes": "PFO,LCA", "suggested_price": 480, "suggested_stops": 0, "suggested_travel_h": 6, "suggested_layover_h": 4},
    {"id": "MT", "label": "Malta", "group": "Europa Południowa", "codes": "MLA", "suggested_price": 450, "suggested_stops": 0, "suggested_travel_h": 5, "suggested_layover_h": 4},
    {"id": "PT", "label": "Portugalia", "group": "Europa Południowa", "codes": "LIS,OPO,FAO,PDL", "suggested_price": 620, "suggested_stops": 1, "suggested_travel_h": 9, "suggested_layover_h": 4},
    {"id": "MADERA", "label": "Madera", "group": "Europa Południowa", "codes": "FNC", "suggested_price": 850, "suggested_stops": 1, "suggested_travel_h": 12, "suggested_layover_h": 5},
    {"id": "AL", "label": "Albania", "group": "Europa Południowa", "codes": "TIA", "suggested_price": 420, "suggested_stops": 0, "suggested_travel_h": 5, "suggested_layover_h": 4},
    {"id": "ME", "label": "Czarnogóra", "group": "Europa Południowa", "codes": "TGD,TIV", "suggested_price": 450, "suggested_stops": 1, "suggested_travel_h": 7, "suggested_layover_h": 4},
    {"id": "BG", "label": "Bułgaria", "group": "Europa Południowa", "codes": "SOF,BOJ,VAR", "suggested_price": 400, "suggested_stops": 0, "suggested_travel_h": 5, "suggested_layover_h": 4},
    # Europa Zachodnia, Środkowa i Północna (Każdy kraj osobno!)
    {"id": "UK", "label": "Wielka Brytania", "group": "Europa Zachodnia i Północna", "codes": "STN,LTN,LGW,LHR,MAN,EDI,BHX,BRS,LPL", "suggested_price": 450, "suggested_stops": 1, "suggested_travel_h": 8, "suggested_layover_h": 4},
    {"id": "FR", "label": "Francja", "group": "Europa Zachodnia i Północna", "codes": "BVA,CDG,ORY,NCE,MRS,LYS", "suggested_price": 450, "suggested_stops": 0, "suggested_travel_h": 6, "suggested_layover_h": 4},
    {"id": "NL", "label": "Holandia", "group": "Europa Zachodnia i Północna", "codes": "EIN,AMS", "suggested_price": 380, "suggested_stops": 0, "suggested_travel_h": 5, "suggested_layover_h": 3},
    {"id": "BE", "label": "Belgia", "group": "Europa Zachodnia i Północna", "codes": "CRL,BRU", "suggested_price": 350, "suggested_stops": 0, "suggested_travel_h": 5, "suggested_layover_h": 3},
    {"id": "DE", "label": "Niemcy", "group": "Europa Zachodnia i Północna", "codes": "BER,FRA,MUC,DTM,CGN,HAM", "suggested_price": 380, "suggested_stops": 0, "suggested_travel_h": 4, "suggested_layover_h": 3},
    {"id": "CH", "label": "Szwajcaria", "group": "Europa Zachodnia i Północna", "codes": "BSL,ZRH,GVA", "suggested_price": 450, "suggested_stops": 0, "suggested_travel_h": 5, "suggested_layover_h": 3},
    {"id": "AT", "label": "Austria", "group": "Europa Środkowa", "codes": "VIE,SZG", "suggested_price": 350, "suggested_stops": 0, "suggested_travel_h": 4, "suggested_layover_h": 3},
    {"id": "CZ", "label": "Czechy", "group": "Europa Środkowa", "codes": "PRG,OSR", "suggested_price": 350, "suggested_stops": 0, "suggested_travel_h": 4, "suggested_layover_h": 3},
    {"id": "HU", "label": "Węgry", "group": "Europa Środkowa", "codes": "BUD", "suggested_price": 350, "suggested_stops": 0, "suggested_travel_h": 4, "suggested_layover_h": 3},
    {"id": "SK", "label": "Słowacja", "group": "Europa Środkowa", "codes": "BTS,KSC", "suggested_price": 320, "suggested_stops": 0, "suggested_travel_h": 4, "suggested_layover_h": 3},
    {"id": "IE", "label": "Irlandia", "group": "Europa Zachodnia i Północna", "codes": "DUB,SNN,ORK", "suggested_price": 420, "suggested_stops": 0, "suggested_travel_h": 5, "suggested_layover_h": 3},
    {"id": "NO", "label": "Norwegia", "group": "Europa Zachodnia i Północna", "codes": "TRF,OSL,BGO,SVG,TOS", "suggested_price": 380, "suggested_stops": 0, "suggested_travel_h": 5, "suggested_layover_h": 3},
    {"id": "SE", "label": "Szwecja", "group": "Europa Zachodnia i Północna", "codes": "ARN,NYO,GOT,MMX", "suggested_price": 360, "suggested_stops": 0, "suggested_travel_h": 5, "suggested_layover_h": 3},
    {"id": "DK", "label": "Dania", "group": "Europa Zachodnia i Północna", "codes": "CPH,BLL,AAR", "suggested_price": 360, "suggested_stops": 0, "suggested_travel_h": 5, "suggested_layover_h": 3},
    {"id": "IS", "label": "Islandia", "group": "Europa Zachodnia i Północna", "codes": "KEF", "suggested_price": 650, "suggested_stops": 1, "suggested_travel_h": 9, "suggested_layover_h": 4},
    # Bliski Wschód, Kaukaz i Afryka Północna
    {"id": "TR", "label": "Turcja", "group": "Bliski Wschód i Afryka", "codes": "AYT,IST,SAW,DLM,BJV,ADB", "suggested_price": 650, "suggested_stops": 1, "suggested_travel_h": 10, "suggested_layover_h": 4},
    {"id": "GE", "label": "Gruzja", "group": "Bliski Wschód i Afryka", "codes": "KUT,TBS,BUS", "suggested_price": 550, "suggested_stops": 1, "suggested_travel_h": 9, "suggested_layover_h": 4},
    {"id": "UAE", "label": "Zjednoczone Emiraty Arabskie (Dubaj)", "group": "Bliski Wschód i Afryka", "codes": "DXB,AUH", "suggested_price": 1100, "suggested_stops": 1, "suggested_travel_h": 12, "suggested_layover_h": 5},
    {"id": "MA", "label": "Maroko", "group": "Bliski Wschód i Afryka", "codes": "RAK,AGA", "suggested_price": 650, "suggested_stops": 1, "suggested_travel_h": 11, "suggested_layover_h": 5},
    {"id": "EG", "label": "Egipt", "group": "Bliski Wschód i Afryka", "codes": "HRG,SSH,CAI", "suggested_price": 850, "suggested_stops": 1, "suggested_travel_h": 11, "suggested_layover_h": 5},
    {"id": "JO", "label": "Jordania", "group": "Bliski Wschód i Afryka", "codes": "AMM,AQJ", "suggested_price": 650, "suggested_stops": 1, "suggested_travel_h": 10, "suggested_layover_h": 5},
    # Ameryka i Azja (Każdy kraj osobno!)
    {"id": "USA", "label": "USA", "group": "Ameryka i Azja", "codes": "JFK,EWR,MIA,ORD,LAX,SFO,BOS", "suggested_price": 2800, "suggested_stops": 1, "suggested_travel_h": 20, "suggested_layover_h": 6},
    {"id": "CA", "label": "Kanada", "group": "Ameryka i Azja", "codes": "YYZ,YUL,YVR", "suggested_price": 2900, "suggested_stops": 1, "suggested_travel_h": 20, "suggested_layover_h": 6},
    {"id": "MX", "label": "Meksyk", "group": "Ameryka i Azja", "codes": "CUN,MEX", "suggested_price": 3400, "suggested_stops": 2, "suggested_travel_h": 24, "suggested_layover_h": 6},
    {"id": "TH", "label": "Tajlandia", "group": "Ameryka i Azja", "codes": "BKK,HKT,KBV,CNX", "suggested_price": 3200, "suggested_stops": 2, "suggested_travel_h": 26, "suggested_layover_h": 6},
    {"id": "SG", "label": "Singapur", "group": "Ameryka i Azja", "codes": "SIN", "suggested_price": 3200, "suggested_stops": 2, "suggested_travel_h": 26, "suggested_layover_h": 6},
    {"id": "MY", "label": "Malezja", "group": "Ameryka i Azja", "codes": "KUL", "suggested_price": 3200, "suggested_stops": 2, "suggested_travel_h": 26, "suggested_layover_h": 6},
    {"id": "ID", "label": "Indonezja (Bali)", "group": "Ameryka i Azja", "codes": "DPS,CGK", "suggested_price": 3600, "suggested_stops": 2, "suggested_travel_h": 28, "suggested_layover_h": 6},
    {"id": "JP", "label": "Japonia", "group": "Ameryka i Azja", "codes": "NRT,HND,KIX", "suggested_price": 3600, "suggested_stops": 2, "suggested_travel_h": 26, "suggested_layover_h": 6},
    {"id": "VN", "label": "Wietnam", "group": "Ameryka i Azja", "codes": "HAN,SGN,DAD,PQC", "suggested_price": 3800, "suggested_stops": 2, "suggested_travel_h": 28, "suggested_layover_h": 8},
    {"id": "MV", "label": "Malediwy", "group": "Ameryka i Azja", "codes": "MLE", "suggested_price": 4200, "suggested_stops": 2, "suggested_travel_h": 26, "suggested_layover_h": 8},
    {"id": "AU", "label": "Australia", "group": "Oceania i Antypody", "codes": "SYD,MEL,BNE,PER,ADL", "suggested_price": 6800, "suggested_stops": 2, "suggested_travel_h": 48, "suggested_layover_h": 16},
    {"id": "NZ", "label": "Nowa Zelandia", "group": "Oceania i Antypody", "codes": "AKL,CHC,WLG,ZQN,DUD,NSN", "suggested_price": 7500, "suggested_stops": 2, "suggested_travel_h": 52, "suggested_layover_h": 16},
    {"id": "PF", "label": "Polinezja Francuska (Tahiti / Bora Bora)", "group": "Oceania i Antypody", "codes": "PPT,BOB,MOZ", "suggested_price": 9500, "suggested_stops": 3, "suggested_travel_h": 56, "suggested_layover_h": 22},
    {"id": "FJ", "label": "Fidżi", "group": "Oceania i Antypody", "codes": "NAN,SUV", "suggested_price": 8500, "suggested_stops": 3, "suggested_travel_h": 54, "suggested_layover_h": 20},
]

# Extra city-level suggestions for instant typeahead (letter-by-letter autocomplete)
CITY_SUGGESTIONS: List[Dict[str, Any]] = [
    {"label": "Warszawa, Polska (WAW)", "codes": "WAW", "preset_id": "PL"},
    {"label": "Kraków, Polska (KRK)", "codes": "KRK", "preset_id": "PL"},
    {"label": "Gdańsk, Polska (GDN)", "codes": "GDN", "preset_id": "PL"},
    {"label": "Wrocław, Polska (WRO)", "codes": "WRO", "preset_id": "PL"},
    {"label": "Szczecin, Polska (SZZ)", "codes": "SZZ", "preset_id": "PL"},
    {"label": "Katowice, Polska (KTW)", "codes": "KTW", "preset_id": "PL"},
    {"label": "Poznań, Polska (POZ)", "codes": "POZ", "preset_id": "PL"},
    {"label": "Rzeszów, Polska (RZE)", "codes": "RZE", "preset_id": "PL"},
    {"label": "Bydgoszcz / Lublin / Łódź / Mazury (BZG / LUZ / LCJ / SZY)", "codes": "BZG,LUZ,LCJ,SZY", "preset_id": "PL"},
    {"label": "Mediolan, Włochy (BGY / MXP)", "codes": "BGY,MXP", "preset_id": "IT"},
    {"label": "Rzym, Włochy (CIA / FCO)", "codes": "CIA,FCO", "preset_id": "IT"},
    {"label": "Wenecja, Włochy (VCE / TSF)", "codes": "VCE,TSF", "preset_id": "IT"},
    {"label": "Bari, Włochy (BRI)", "codes": "BRI", "preset_id": "IT"},
    {"label": "Neapol, Włochy (NAP)", "codes": "NAP", "preset_id": "IT"},
    {"label": "Florencja / Piza, Włochy (FLR / PSA)", "codes": "FLR,PSA", "preset_id": "IT"},
    {"label": "Sycylia – Katania / Palermo (CTA / PMO)", "codes": "CTA,PMO", "preset_id": "IT"},
    {"label": "Bolonia / Piza, Włochy (BLQ / PSA)", "codes": "BLQ,PSA", "preset_id": "IT"},
    {"label": "Barcelona, Hiszpania (BCN / GRO)", "codes": "BCN,GRO", "preset_id": "ES"},
    {"label": "Madryt, Hiszpania (MAD)", "codes": "MAD", "preset_id": "ES"},
    {"label": "Walencja / Sewilla / Bilbao, Hiszpania (VLC / SVQ / BIO)", "codes": "VLC,SVQ,BIO", "preset_id": "ES"},
    {"label": "Malaga, Hiszpania (AGP)", "codes": "AGP", "preset_id": "ES"},
    {"label": "Alicante, Hiszpania (ALC)", "codes": "ALC", "preset_id": "ES"},
    {"label": "Majorka – Palma, Hiszpania (PMI)", "codes": "PMI", "preset_id": "ES"},
    {"label": "Teneryfa, Wyspy Kanaryjskie (TFS)", "codes": "TFS", "preset_id": "ES"},
    {"label": "Gran Canaria / Fuerteventura (LPA / FUE)", "codes": "LPA,FUE", "preset_id": "ES"},
    {"label": "Ateny, Grecja (ATH)", "codes": "ATH", "preset_id": "GR"},
    {"label": "Kreta – Chania / Heraklion (CHQ / HER)", "codes": "CHQ,HER", "preset_id": "GR"},
    {"label": "Rodos, Grecja (RHO)", "codes": "RHO", "preset_id": "GR"},
    {"label": "Korfu, Grecja (CFU)", "codes": "CFU", "preset_id": "GR"},
    {"label": "Santorini / Zakynthos (JTR / ZTH)", "codes": "JTR,ZTH", "preset_id": "GR"},
    {"label": "Pafos, Cypr (PFO)", "codes": "PFO", "preset_id": "CY"},
    {"label": "Larnaka, Cypr (LCA)", "codes": "LCA", "preset_id": "CY"},
    {"label": "Zadar / Split / Dubrownik, Chorwacja", "codes": "ZAD,SPU,DBV", "preset_id": "HR"},
    {"label": "Lizbona, Portugalia (LIS)", "codes": "LIS", "preset_id": "PT"},
    {"label": "Porto, Portugalia (OPO)", "codes": "OPO", "preset_id": "PT"},
    {"label": "Faro – Algarve, Portugalia (FAO)", "codes": "FAO", "preset_id": "PT"},
    {"label": "Funchal – Madera (FNC)", "codes": "FNC", "preset_id": "MADERA"},
    {"label": "Antalya, Turcja (AYT)", "codes": "AYT", "preset_id": "TR"},
    {"label": "Stambuł, Turcja (IST / SAW)", "codes": "IST,SAW", "preset_id": "TR"},
    {"label": "Londyn, Wielka Brytania (STN / LTN / LGW / LHR)", "codes": "STN,LTN,LGW,LHR", "preset_id": "UK"},
    {"label": "Edynburg, Wielka Brytania / Szkocja (EDI)", "codes": "EDI", "preset_id": "UK"},
    {"label": "Glasgow, Wielka Brytania / Szkocja (GLA)", "codes": "GLA", "preset_id": "UK"},
    {"label": "Manchester, Wielka Brytania (MAN)", "codes": "MAN", "preset_id": "UK"},
    {"label": "Bristol / Birmingham / Liverpool (BRS / BHX / LPL)", "codes": "BRS,BHX,LPL", "preset_id": "UK"},
    {"label": "Berlin / Monachium / Frankfurt, Niemcy (BER / MUC / FRA)", "codes": "BER,MUC,FRA", "preset_id": "DE"},
    {"label": "Zurych / Genewa / Bazylea, Szwajcaria (ZRH / GVA / BSL)", "codes": "ZRH,GVA,BSL", "preset_id": "CH"},
    {"label": "Paryż, Francja (BVA / CDG / ORY)", "codes": "BVA,CDG,ORY", "preset_id": "FR"},
    {"label": "Nicea, Francja (NCE)", "codes": "NCE", "preset_id": "FR"},
    {"label": "Eindhoven / Amsterdam, Holandia (EIN / AMS)", "codes": "EIN,AMS", "preset_id": "NL"},
    {"label": "Bruksela, Belgia (CRL / BRU)", "codes": "CRL,BRU", "preset_id": "BE"},
    {"label": "Wiedeń, Austria (VIE)", "codes": "VIE", "preset_id": "AT"},
    {"label": "Praga, Czechy (PRG)", "codes": "PRG", "preset_id": "CZ"},
    {"label": "Budapeszt, Węgry (BUD)", "codes": "BUD", "preset_id": "HU"},
    {"label": "Bratysława, Słowacja (BTS)", "codes": "BTS", "preset_id": "SK"},
    {"label": "Kopenhaga / Billund, Dania (CPH / BLL)", "codes": "CPH,BLL", "preset_id": "DK"},
    {"label": "Sztokholm, Szwecja (ARN / NYO)", "codes": "ARN,NYO", "preset_id": "SE"},
    {"label": "Oslo / Bergen, Norwegia (TRF / OSL / BGO)", "codes": "TRF,OSL,BGO", "preset_id": "NO"},
    {"label": "Dubaj / Abu Zabi, ZEA (DXB / AUH)", "codes": "DXB,AUH", "preset_id": "UAE"},
    {"label": "Marrakesz / Agadir, Maroko (RAK / AGA)", "codes": "RAK,AGA", "preset_id": "MA"},
    {"label": "Hurghada / Sharm el-Sheikh, Egipt (HRG / SSH)", "codes": "HRG,SSH", "preset_id": "EG"},
    {"label": "Nowy Jork, USA (JFK / EWR)", "codes": "JFK,EWR", "preset_id": "USA"},
    {"label": "Miami, USA (MIA)", "codes": "MIA", "preset_id": "USA"},
    {"label": "Chicago, USA (ORD)", "codes": "ORD", "preset_id": "USA"},
    {"label": "Los Angeles / San Francisco, USA (LAX / SFO)", "codes": "LAX,SFO", "preset_id": "USA"},
    {"label": "Bangkok, Tajlandia (BKK)", "codes": "BKK", "preset_id": "TH"},
    {"label": "Phuket / Krabi, Tajlandia (HKT / KBV)", "codes": "HKT,KBV", "preset_id": "TH"},
    {"label": "Singapur – Changi (SIN)", "codes": "SIN", "preset_id": "SG"},
    {"label": "Kuala Lumpur, Malezja (KUL)", "codes": "KUL", "preset_id": "MY"},
    {"label": "Bali – Denpasar, Indonezja (DPS)", "codes": "DPS", "preset_id": "ID"},
    {"label": "Tokio, Japonia (NRT / HND)", "codes": "NRT,HND", "preset_id": "JP"},
    {"label": "Hanoi / Ho Chi Minh (Sajgon), Wietnam (HAN / SGN)", "codes": "HAN,SGN", "preset_id": "VN"},
    {"label": "Male, Malediwy (MLE)", "codes": "MLE", "preset_id": "MV"},
    {"label": "Sydney / Melbourne, Australia (SYD / MEL)", "codes": "SYD,MEL", "preset_id": "AU"},
    {"label": "Auckland / Christchurch / Queenstown, Nowa Zelandia (AKL / CHC / ZQN)", "codes": "AKL,CHC,ZQN", "preset_id": "NZ"},
    {"label": "Bora Bora / Tahiti (Papeete), Polinezja Francuska (BOB / PPT)", "codes": "BOB,PPT", "preset_id": "PF"},
    {"label": "Nadi, Fidżi (NAN)", "codes": "NAN", "preset_id": "FJ"},
]

KEYWORD_TO_CODES: Dict[str, List[str]] = {
    "usa": ["JFK", "EWR", "MIA", "ORD", "LAX", "SFO", "BOS"],
    "stany": ["JFK", "EWR", "MIA", "ORD", "LAX", "SFO", "BOS"],
    "ameryka": ["JFK", "EWR", "MIA", "ORD", "LAX", "SFO", "BOS"],
    "nowy jork": ["JFK", "EWR"],
    "miami": ["MIA"],
    "kanada": ["YYZ", "YUL", "YVR"],
    "meksyk": ["CUN", "MEX"],
    "japonia": ["NRT", "HND", "KIX"],
    "tokio": ["NRT", "HND"],
    "wietnam": ["HAN", "SGN", "DAD", "PQC"],
    "hanoi": ["HAN"],
    "sajgon": ["SGN"],
    "malediwy": ["MLE"],
    "male": ["MLE"],
    "australia": ["SYD", "MEL", "BNE", "PER", "ADL"],
    "sydney": ["SYD"],
    "melbourne": ["MEL"],
    "nowa zelandia": ["AKL", "CHC", "WLG", "ZQN", "DUD", "NSN"],
    "auckland": ["AKL"],
    "christchurch": ["CHC"],
    "wellington": ["WLG"],
    "queenstown": ["ZQN"],
    "polinezja francuska": ["PPT", "BOB", "MOZ"],
    "polinezja": ["PPT", "BOB", "MOZ"],
    "bora bora": ["BOB"],
    "tahiti": ["PPT"],
    "fidzi": ["NAN", "SUV"],
    "fiji": ["NAN", "SUV"],
    "anglia": ["STN", "LTN", "LGW", "LHR", "MAN", "EDI", "BHX", "BRS", "LPL"],
    "uk": ["STN", "LTN", "LGW", "LHR", "MAN", "EDI", "BHX", "BRS", "LPL"],
    "wielka brytania": ["STN", "LTN", "LGW", "LHR", "MAN", "EDI", "BHX", "BRS", "LPL"],
    "szkocja": ["EDI", "GLA", "ABZ"],
    "edynburg": ["EDI"],
    "edinburgh": ["EDI"],
    "glasgow": ["GLA"],
    "manchester": ["MAN"],
    "bristol": ["BRS"],
    "birmingham": ["BHX"],
    "liverpool": ["LPL"],
    "londyn": ["STN", "LTN", "LGW", "LHR"],
    "berlin": ["BER"],
    "monachium": ["MUC"],
    "frankfurt": ["FRA"],
    "zurych": ["ZRH"],
    "genewa": ["GVA"],
    "bazylea": ["BSL"],
    "kopenhaga": ["CPH"],
    "sztokholm": ["ARN", "NYO"],
    "oslo": ["TRF", "OSL"],
    "paryz": ["BVA", "CDG", "ORY"],
    "nicea": ["NCE"],
    "wenecja": ["VCE", "TSF"],
    "neapol": ["NAP"],
    "bari": ["BRI"],
    "bolonia": ["BLQ"],
    "florencja": ["FLR"],
    "walencja": ["VLC"],
    "sewilla": ["SVQ"],
    "malaga": ["AGP"],
    "alicante": ["ALC"],
    "turcja": ["AYT", "IST", "SAW", "DLM", "BJV", "ADB"],
    "antalya": ["AYT"],
    "stambuł": ["IST", "SAW"],
    "stambul": ["IST", "SAW"],
    "grecja": ["ATH", "SKG", "CFU", "CHQ", "HER", "RHO", "ZTH", "KGS", "JTR"],
    "ateny": ["ATH"],
    "kreta": ["CHQ", "HER"],
    "rodos": ["RHO"],
    "korfu": ["CFU"],
    "tajlandia": ["BKK", "HKT", "KBV", "CNX"],
    "bangkok": ["BKK"],
    "phuket": ["HKT"],
    "bali": ["DPS", "CGK"],
    "indonezja": ["DPS", "CGK"],
    "singapur": ["SIN"],
    "malezja": ["KUL"],
    "kuala lumpur": ["KUL"],
    "madera": ["FNC"],
    "funchal": ["FNC"],
    "portugalia": ["LIS", "OPO", "FAO", "PDL"],
    "lizbona": ["LIS"],
    "porto": ["OPO"],
    "hiszpania": ["BCN", "GRO", "MAD", "AGP", "ALC", "VLC", "PMI", "TFS", "LPA", "FUE", "IBZ", "SVQ"],
    "barcelona": ["BCN", "GRO"],
    "madryt": ["MAD"],
    "majorka": ["PMI"],
    "teneryfa": ["TFS"],
    "kanary": ["TFS", "LPA", "FUE", "ACE"],
    "włochy": ["BGY", "MXP", "CIA", "FCO", "BRI", "NAP", "CTA", "PMO", "BLQ", "PSA", "VCE", "TSF"],
    "wlochy": ["BGY", "MXP", "CIA", "FCO", "BRI", "NAP", "CTA", "PMO", "BLQ", "PSA", "VCE", "TSF"],
    "mediolan": ["BGY", "MXP"],
    "rzym": ["CIA", "FCO"],
    "dubaj": ["DXB", "AUH"],
    "zea": ["DXB", "AUH"],
    "maroko": ["RAK", "AGA"],
    "egipt": ["HRG", "SSH", "CAI"],
    "jordania": ["AMM", "AQJ"],
    "gruzja": ["KUT", "TBS", "BUS"],
    "cypr": ["PFO", "LCA"],
    "pafos": ["PFO"],
    "malta": ["MLA"],
    "chorwacja": ["ZAD", "SPU", "DBV", "ZAG", "PUY"],
    "albania": ["TIA"],
    "czarnogóra": ["TGD", "TIV"],
    "czarnogora": ["TGD", "TIV"],
    "bułgaria": ["SOF", "BOJ", "VAR"],
    "bulgaria": ["SOF", "BOJ", "VAR"],
    "francja": ["BVA", "CDG", "ORY", "NCE", "MRS", "LYS"],
    "norwegia": ["TRF", "OSL", "BGO", "SVG", "TOS"],
    "szwecja": ["ARN", "NYO", "GOT", "MMX"],
    "dania": ["CPH", "BLL", "AAR"],
    "islandia": ["KEF"],
    "holandia": ["EIN", "AMS"],
    "belgia": ["CRL", "BRU"],
    "niemcy": ["BER", "FRA", "MUC", "DTM", "CGN", "HAM"],
    "szwajcaria": ["BSL", "ZRH", "GVA"],
    "austria": ["VIE", "SZG"],
    "wiedeń": ["VIE"],
    "wieden": ["VIE"],
    "czechy": ["PRG", "OSR"],
    "praga": ["PRG"],
    "węgry": ["BUD"],
    "wegry": ["BUD"],
    "budapeszt": ["BUD"],
    "słowacja": ["BTS", "KSC"],
    "slowacja": ["BTS", "KSC"],
    "irlandia": ["DUB", "SNN", "ORK"],
    "polska": ["WAW", "KRK", "GDN", "WRO", "POZ", "KTW", "RZE", "SZZ", "BZG", "LUZ", "SZY", "IEG"],
    "loty krajowe": ["WAW", "KRK", "GDN", "WRO", "POZ", "KTW", "RZE", "SZZ", "BZG", "LUZ", "SZY", "IEG"],
    "krajowe": ["WAW", "KRK", "GDN", "WRO", "POZ", "KTW", "RZE", "SZZ", "BZG", "LUZ", "SZY", "IEG"],
    "pl": ["WAW", "KRK", "GDN", "WRO", "POZ", "KTW", "RZE", "SZZ", "BZG", "LUZ", "SZY", "IEG"],
    "warszawa": ["WAW"],
    "kraków": ["KRK"],
    "krakow": ["KRK"],
    "gdańsk": ["GDN"],
    "gdansk": ["GDN"],
    "wrocław": ["WRO"],
    "wroclaw": ["WRO"],
    "katowice": ["KTW"],
    "poznań": ["POZ"],
    "poznan": ["POZ"],
    "szczecin": ["SZZ"],
    "rzeszów": ["RZE"],
    "rzeszow": ["RZE"],
    "bydgoszcz": ["BZG"],
    "lublin": ["LUZ"],
    "olsztyn": ["SZY"],
    "mazury": ["SZY"],
    "zielona góra": ["IEG"],
    "zielona gora": ["IEG"],
}

AIRPORT_COORDS: Dict[str, tuple[float, float]] = {
    **{k: (v["lat"], v["lon"]) for k, v in POLISH_AIRPORTS.items()},
    "IEG": (52.1385, 15.7986),
    "SZY": (53.4819, 20.9377),
    "BGY": (45.6739, 9.7042),
    "MXP": (45.6306, 8.7281),
    "CIA": (41.7994, 12.5949),
    "FCO": (41.8003, 12.2389),
    "BRI": (41.1389, 16.7606),
    "NAP": (40.8860, 14.2908),
    "CTA": (37.4668, 15.0664),
    "PMO": (38.1759, 13.0910),
    "BLQ": (44.5354, 11.2887),
    "PSA": (43.6839, 10.3927),
    "VCE": (45.5053, 12.3519),
    "TSF": (45.6484, 12.1944),
    "BCN": (41.2974, 2.0833),
    "GRO": (41.9010, 2.7606),
    "MAD": (40.4983, -3.5676),
    "AGP": (36.6749, -4.4991),
    "ALC": (38.2822, -0.5582),
    "VLC": (39.4893, -0.4816),
    "PMI": (39.5517, 2.7388),
    "IBZ": (38.8729, 1.3731),
    "SVQ": (37.4180, -5.8931),
    "TFS": (28.0445, -16.5725),
    "LPA": (27.9319, -15.3866),
    "FUE": (28.4527, -13.8638),
    "ACE": (28.9455, -13.6052),
    "FNC": (32.6979, -16.7745),
    "PDL": (37.7412, -25.6979),
    "LIS": (38.7756, -9.1354),
    "OPO": (41.2481, -8.6814),
    "FAO": (37.0144, -7.9659),
    "STN": (51.8850, 0.2350),
    "LTN": (51.8747, -0.3683),
    "LGW": (51.1537, -0.1821),
    "LHR": (51.4700, -0.4543),
    "MAN": (53.3537, -2.2750),
    "EDI": (55.9500, -3.3725),
    "BHX": (52.4539, -1.7480),
    "BRS": (51.3827, -2.7191),
    "LPL": (53.3336, -2.8497),
    "DUB": (53.4213, -6.2701),
    "IST": (41.2753, 28.7519),
    "SAW": (40.8986, 29.3092),
    "AYT": (36.8987, 30.8005),
    "DLM": (36.7131, 28.7925),
    "BJV": (37.2506, 27.6643),
    "ADB": (38.2924, 27.1570),
    "ATH": (37.9364, 23.9445),
    "SKG": (40.5197, 22.9709),
    "CFU": (39.6019, 19.9117),
    "CHQ": (35.5317, 24.1497),
    "HER": (35.3397, 25.1803),
    "RHO": (36.4054, 28.0862),
    "ZTH": (37.7509, 20.8843),
    "KGS": (36.7933, 27.0917),
    "JTR": (36.3992, 25.4793),
    "JFK": (40.6413, -73.7781),
    "EWR": (40.6895, -74.1745),
    "MIA": (25.7959, -80.2870),
    "ORD": (41.9742, -87.9073),
    "LAX": (33.9416, -118.4085),
    "SFO": (37.6213, -122.3790),
    "BOS": (42.3656, -71.0096),
    "YYZ": (43.6777, -79.6248),
    "YUL": (45.4657, -73.7455),
    "CUN": (21.0365, -86.8771),
    "BKK": (13.6900, 100.7501),
    "HKT": (8.1132, 98.3169),
    "KBV": (8.0950, 98.9862),
    "CNX": (18.7668, 98.9626),
    "SIN": (1.3644, 103.9915),
    "KUL": (2.7456, 101.7072),
    "DPS": (-8.7482, 115.1672),
    "CGK": (-6.1256, 106.6559),
    "NRT": (35.7720, 140.3929),
    "HND": (35.5494, 139.7798),
    "DXB": (25.2532, 55.3657),
    "AUH": (24.4330, 54.6511),
    "RAK": (31.6069, -8.0363),
    "AGA": (30.3250, -9.4131),
    "HRG": (27.1783, 33.7994),
    "SSH": (27.9773, 34.3950),
    "CAI": (30.1219, 31.4056),
    "AMM": (31.7226, 35.9932),
    "AQJ": (29.6116, 35.0181),
    "KUT": (42.1767, 42.4826),
    "TBS": (41.6692, 44.9547),
    "BVA": (49.4544, 2.1128),
    "CDG": (49.0097, 2.5479),
    "NCE": (43.6584, 7.2159),
    "CRL": (50.4592, 4.4538),
    "BRU": (50.9010, 4.4856),
    "EIN": (51.4501, 5.3745),
    "AMS": (52.3105, 4.7683),
    "BER": (52.3667, 13.5033),
    "FRA": (50.0379, 8.5622),
    "MUC": (48.3537, 11.7750),
    "DTM": (51.5183, 7.6122),
    "BSL": (47.5900, 7.5299),
    "ZRH": (47.4582, 8.5555),
    "GVA": (46.2370, 6.1092),
    "CPH": (55.6180, 12.6508),
    "BLL": (55.7403, 9.1518),
    "ARN": (59.6519, 17.9186),
    "GOT": (57.6628, 12.2798),
    "TRF": (59.1867, 10.2586),
    "OSL": (60.1976, 11.1004),
    "BGO": (60.2934, 5.2181),
    "KEF": (63.9850, -22.6056),
    "BUD": (47.4369, 19.2556),
    "VIE": (48.1103, 16.5697),
    "PRG": (50.1008, 14.2600),
    "BTS": (48.1702, 17.2127),
    "KSC": (48.6631, 21.2411),
    "ZAD": (44.1083, 15.3467),
    "SPU": (43.5389, 16.2980),
    "DBV": (42.5614, 18.2682),
    "MLA": (35.8575, 14.4775),
    "PFO": (34.7180, 32.4857),
    "LCA": (34.8751, 33.6249),
    "TIA": (41.4147, 19.7206),
    "TGD": (42.3594, 19.2519),
    "TIV": (42.4047, 18.7233),
    "SOF": (42.6952, 23.4062),
    "BOJ": (42.5696, 27.5152),
    "VAR": (43.2321, 27.8251),
}

def _norm_text(s: Optional[str]) -> str:
    text = (s or "").strip().lower().replace("ł", "l")
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )


_WORLD_AIRPORTS_FILE = Path(__file__).resolve().parent / "data" / "world_airports.json"
WORLD_AIRPORTS: List[Dict[str, Any]] = []
_WORLD_BY_CODE: Dict[str, Dict[str, Any]] = {}
_COUNTRY_NAME_BY_CC: Dict[str, str] = {}
_WORLD_SEARCH_ROWS: List[tuple[Dict[str, Any], str, str, str, str]] = []
_LIVE_PLACES_CACHE: Dict[str, List[Dict[str, Any]]] = {}

if _WORLD_AIRPORTS_FILE.exists():
    try:
        WORLD_AIRPORTS = json.loads(_WORLD_AIRPORTS_FILE.read_text(encoding="utf-8"))
        for item in WORLD_AIRPORTS:
            c = (item.get("c") or "").upper()
            if not c:
                continue
            _WORLD_BY_CODE[c] = item
            if item.get("cc") and item.get("cn") and item["cc"].upper() not in _COUNTRY_NAME_BY_CC:
                _COUNTRY_NAME_BY_CC[item["cc"].upper()] = item["cn"]
            if c not in AIRPORT_COORDS and item.get("lat") is not None and item.get("lon") is not None:
                AIRPORT_COORDS[c] = (float(item["lat"]), float(item["lon"]))
            city_n = _norm_text(item.get("city"))
            cn_n = _norm_text(item.get("cn"))
            alias_n = _norm_text(item.get("a"))
            full_n = f"{c.lower()} {city_n} {cn_n} {_norm_text(item.get('n'))} {alias_n}".strip()
            _WORLD_SEARCH_ROWS.append((item, c.lower(), city_n, cn_n, full_n))
    except Exception:
        WORLD_AIRPORTS = []


def get_airport_meta(code: str) -> Dict[str, str]:
    c = (code or "").strip().upper()
    pl = POLISH_AIRPORTS.get(c)
    ap = _WORLD_BY_CODE.get(c)
    if pl:
        return {
            "city": pl["city"],
            "name": pl["name"],
            "country": "Polska",
            "country_code": "PL",
        }
    if ap:
        return {
            "city": ap.get("city") or c,
            "name": ap.get("n") or ap.get("city") or c,
            "country": ap.get("cn") or ap.get("cc") or "",
            "country_code": (ap.get("cc") or "").upper(),
        }
    return {"city": c, "name": c, "country": "", "country_code": ""}


def get_destination_weather(dest_iata: str, date_str: str) -> Dict[str, Any]:
    code = dest_iata.upper()
    try:
        month = int(date_str[5:7])
    except Exception:
        month = 11

    if code in ("BKK", "HKT", "KBV", "CNX", "SIN", "KUL", "DPS", "CGK", "CUN"):
        temp = 31 if month in (11, 12, 1, 2, 3, 4) else 30
    elif code in ("DXB", "AUH", "HRG", "SSH", "CAI", "AQJ", "MIA"):
        temp = 27 if month in (11, 12, 1, 2, 3) else 33
    elif code in ("TFS", "LPA", "FUE", "ACE", "FNC", "AGA", "RAK"):
        temp = 22 if month in (11, 12, 1, 2, 3) else 26
    elif code in ("MLA", "PFO", "LCA", "AGP", "ALC", "CTA", "PMO", "AYT", "RHO", "CHQ", "HER"):
        temp = 19 if month in (11, 12, 1, 2) else (23 if month in (3, 4, 10) else 29)
    elif code in ("CIA", "FCO", "BCN", "LIS", "OPO", "FAO", "ATH", "NAP", "BRI", "PMI", "VLC", "MAD", "IST", "SAW", "TIA", "TGD", "TIV"):
        temp = 15 if month in (11, 12, 1, 2) else (20 if month in (3, 4, 10) else 27)
    elif code in ("JFK", "EWR", "ORD", "BOS", "YYZ", "YUL"):
        temp = 6 if month in (11, 12, 1, 2, 3) else 23
    elif code in ("LAX", "SFO", "NRT", "HND"):
        temp = 17 if month in (11, 12, 1, 2, 3) else 24
    elif code in POLISH_AIRPORTS or code in ("IEG", "SZY"):
        if month in (12, 1, 2):
            temp = 2
        elif month in (11, 3):
            temp = 6
        elif month in (4, 10):
            temp = 12
        else:
            temp = 21
    else:
        lat = AIRPORT_COORDS.get(code, (48.0, 12.0))[0]
        alat = abs(lat)
        if alat <= 23.5:
            temp = 29
        elif alat <= 35.0:
            temp = 21 if month in (11, 12, 1, 2, 3) else 27
        elif alat >= 55.0:
            temp = 3 if month in (11, 12, 1, 2, 3) else 17
        elif alat >= 48.0:
            temp = 7 if month in (11, 12, 1, 2, 3) else 20
        else:
            temp = 13 if month in (11, 12, 1, 2, 3) else 24

    return {
        "temp_c": temp,
        "is_warm": temp >= 18,
    }


def get_destination_cost_info(dest_iata: str, country_code: str = "") -> Dict[str, Any]:
    code = dest_iata.upper()
    cc = (country_code or "").upper()

    if code in ("AYT", "IST", "SAW", "DLM", "BJV", "ADB", "TIA", "TGD", "TIV", "SOF", "BOJ", "VAR", "OTP", "BUD", "BTS", "KSC", "KUT", "TBS", "RAK", "AGA", "HRG", "SSH", "CAI", "BKK", "HKT", "KBV", "CNX", "KUL", "DPS", "CGK") or cc in ("TR", "AL", "ME", "BG", "RO", "HU", "MA", "EG", "TH", "MY", "ID", "SK", "GE"):
        return {
            "cost_tier": "LOW",
            "cost_label": "Tani na miejscu",
            "est_night_pln": 95,
        }
    if code in ("JFK", "EWR", "MIA", "ORD", "LAX", "SFO", "BOS", "YYZ", "YUL", "CPH", "BLL", "ARN", "GOT", "OSL", "TRF", "BGO", "KEF", "BSL", "ZRH", "GVA", "LHR", "LGW", "STN", "LTN", "EDI", "DUB", "SIN", "DXB", "AMS") or cc in ("US", "CA", "DK", "NO", "SE", "CH", "IS", "GB", "IE", "SG", "AE", "NL"):
        return {
            "cost_tier": "HIGH",
            "cost_label": "Wyższe ceny na miejscu",
            "est_night_pln": 220,
        }
    return {
        "cost_tier": "MID",
        "cost_label": "Umiarkowane ceny",
        "est_night_pln": 145,
    }


def estimate_baggage_fee_pln(
    carrier_code: str,
    stops_count: int,
    trip_type: str,
    baggage_mode: str = "backpack",
    group_size: int = 1,
) -> tuple[float, str]:
    legs = (1 + max(0, stops_count)) * (2 if trip_type == "roundtrip" else 1)
    is_full_service = carrier_code in ("LO", "LH", "LX", "OS", "KL", "AF", "BA", "SK", "TK", "QR", "EK", "AY")
    group_size = max(1, int(group_size))

    if baggage_mode == "cabin10kg":
        if is_full_service:
            return 0.0, "Walizka kabinowa 10 kg (w cenie linii)"
        fee = (95.0 if carrier_code in ("FR", "W6", "W4", "U2", "D8", "DY") else 75.0) * legs
        return round(fee, 2), f"Z walizką kabinową 10 kg (+{int(fee)} zł/os.)"

    if baggage_mode == "checked20kg":
        fee = (165.0 if not is_full_service else 145.0) * legs
        return round(fee, 2), f"Z walizką rejestrowaną 20 kg (+{int(fee)} zł/os.)"

    if baggage_mode == "checked32kg":
        fee = (240.0 if not is_full_service else 210.0) * legs
        return round(fee, 2), f"Z dużą walizką 23–32 kg (+{int(fee)} zł/os.)"

    if baggage_mode == "shared20kg":
        single_bag_fee = (165.0 if not is_full_service else 145.0) * legs
        per_person = round(single_bag_fee / group_size, 2)
        return per_person, f"1 wspólna walizka 20 kg na Ekipę (+{int(round(per_person))} zł/os.)"

    return 0.0, "Mały plecak pod fotel (0 zł)"


def is_weekend_friendly(
    outbound_dt: str,
    inbound_dt: Optional[str],
    inbound_arrival_dt: Optional[str] = None,
) -> bool:
    """True if outbound is Thu >=16:00, Fri >=14:30, or Sat <=13:30 and return lands Sun >=13:00 or Mon <=09:00."""
    if not outbound_dt:
        return False
    try:
        out = datetime.fromisoformat(outbound_dt[:19])
        out_ok = (
            (out.weekday() == 3 and out.hour >= 16)
            or (out.weekday() == 4 and (out.hour > 14 or (out.hour == 14 and out.minute >= 30)))
            or (out.weekday() == 5 and (out.hour < 13 or (out.hour == 13 and out.minute <= 30)))
        )
        if not out_ok:
            return False
        if not inbound_dt:
            return True
        inb = datetime.fromisoformat(inbound_dt[:19])
        in_arr = datetime.fromisoformat(inbound_arrival_dt[:19]) if inbound_arrival_dt else inb
        nights = (inb.date() - out.date()).days
        if nights < 1 or nights > 4:
            return False
        # Return: Sunday afternoon/evening OR Monday landing before 09:15 (before work)
        in_ok = (
            (inb.weekday() == 6 and inb.hour >= 13)
            or (in_arr.weekday() == 0 and (in_arr.hour < 9 or (in_arr.hour == 9 and in_arr.minute <= 15)))
        )
        return in_ok
    except Exception:
        return False


def is_comfortable_hours(
    outbound_dep: str,
    outbound_arr: str,
    inbound_dep: Optional[str] = None,
    inbound_arr: Optional[str] = None,
) -> bool:
    """Filters out brutal early-morning (<07:00) or middle-of-the-night (>23:00 dep / 00:00-06:00 arr) flights."""
    def _dep_ok(dt_str: Optional[str]) -> bool:
        if not dt_str:
            return True
        try:
            dt = datetime.fromisoformat(dt_str[:19])
            minutes = dt.hour * 60 + dt.minute
            return 420 <= minutes <= 1350  # 07:00 - 22:30
        except Exception:
            return True

    def _arr_ok(dt_str: Optional[str]) -> bool:
        if not dt_str:
            return True
        try:
            dt = datetime.fromisoformat(dt_str[:19])
            minutes = dt.hour * 60 + dt.minute
            return 420 <= minutes <= 1425  # 07:00 - 23:45
        except Exception:
            return True

    return (
        _dep_ok(outbound_dep)
        and _arr_ok(outbound_arr)
        and _dep_ok(inbound_dep)
        and _arr_ok(inbound_arr)
    )


def resolve_destination_iata_list(dest_query: Optional[str]) -> List[str]:
    if not dest_query or not dest_query.strip():
        return []
    raw = dest_query.strip()
    lower = raw.lower()
    norm_full = _norm_text(raw)
    if lower in ("world", "cały świat", "caly swiat", "świat", "europe"):
        return []
    if lower in KEYWORD_TO_CODES:
        return list(KEYWORD_TO_CODES[lower])
    if norm_full in KEYWORD_TO_CODES:
        return list(KEYWORD_TO_CODES[norm_full])

    codes: List[str] = []
    for part in raw.replace(";", ",").split(","):
        tok = part.strip()
        if not tok:
            continue
        tok_low = tok.lower()
        tok_norm = _norm_text(tok)
        if tok_low in KEYWORD_TO_CODES:
            for c in KEYWORD_TO_CODES[tok_low]:
                if c not in codes:
                    codes.append(c)
        elif tok_norm in KEYWORD_TO_CODES:
            for c in KEYWORD_TO_CODES[tok_norm]:
                if c not in codes:
                    codes.append(c)
        elif len(tok) == 3 and tok.isalpha():
            up = tok.upper()
            if up not in codes:
                codes.append(up)
        else:
            # Extract explicit IATA codes inside formatted labels e.g. "Edynburg (EDI)"
            paren_codes = re.findall(r"\b([A-Z]{3})\b", tok)
            if paren_codes:
                for c in paren_codes:
                    if c not in codes:
                        codes.append(c)
            else:
                hits = search_world_airports(tok, limit=2, include_presets=True)
                for h in hits:
                    if h.get("codes"):
                        for c in h["codes"].split(","):
                            up = c.strip().upper()
                            if up and len(up) == 3 and up.isalpha() and up not in codes:
                                codes.append(up)
                        break
    return codes


def format_duration_hm(seconds: Optional[int]) -> str:
    if not seconds or seconds <= 0:
        return "—"
    total_min = int(round(seconds / 60))
    h = total_min // 60
    m = total_min % 60
    if h > 0 and m > 0:
        return f"{h}h {m:02d}m"
    if h > 0:
        return f"{h}h"
    return f"{m}m"


def country_code_to_flag(code: Optional[str]) -> str:
    if not code or len(code) != 2:
        return "EU"
    return code.upper()


_PRESET_BY_ID: Dict[str, Dict[str, Any]] = {p["id"]: p for p in WORLD_PRESETS}


def _estimate_defaults_by_codes(codes_str: str, preset_id: str = "") -> Dict[str, int]:
    if preset_id and preset_id in _PRESET_BY_ID:
        p = _PRESET_BY_ID[preset_id]
        base_p = int(p.get("suggested_price") or 1200)
        trav_h = int(p.get("suggested_travel_h") or 12)
        n_min, n_max = 2, 6
        if trav_h >= 40 or preset_id in ("NZ", "AU"):
            n_min, n_max = 7, 21
        elif preset_id in ("USA", "CA", "MX", "TH", "ID", "SG", "MY", "JP", "VN", "MV") or base_p >= 2800 and preset_id != "WORLD":
            n_min, n_max = 5, 14
        elif preset_id == "WORLD":
            n_min, n_max = 2, 12
        elif preset_id == "PL":
            n_min, n_max = 1, 4
        elif preset_id in ("MADERA", "PT", "TR", "GR", "CY", "MT", "UAE", "MA", "EG", "JO", "IS", "GE"):
            n_min, n_max = 3, 8
        return {
            "dist_km": 0,
            "suggested_price": base_p,
            "suggested_stops": int(p.get("suggested_stops") if p.get("suggested_stops") is not None else 1),
            "suggested_travel_h": trav_h,
            "suggested_layover_h": int(p.get("suggested_layover_h") or 6),
            "suggested_nights_min": n_min,
            "suggested_nights_max": n_max,
        }

    first_code = ""
    for part in (codes_str or "").split(","):
        tok = part.strip().upper()
        if len(tok) == 3 and tok.isalpha():
            first_code = tok
            break
    dist_km = estimate_distance_km("WAW", first_code) if first_code else None
    pop = int((_WORLD_BY_CODE.get(first_code) or {}).get("p") or 0) if first_code else 0
    if dist_km is None:
        return {
            "dist_km": 0,
            "suggested_price": 2800,
            "suggested_stops": 2,
            "suggested_travel_h": 28,
            "suggested_layover_h": 8,
            "suggested_nights_min": 3,
            "suggested_nights_max": 12,
        }
    if dist_km >= 14000 or (dist_km >= 10000 and pop < 800000):
        return {
            "dist_km": dist_km,
            "suggested_price": 9500,
            "suggested_stops": 3,
            "suggested_travel_h": 56,
            "suggested_layover_h": 22,
            "suggested_nights_min": 5,
            "suggested_nights_max": 21,
        }
    if dist_km >= 11500:
        return {
            "dist_km": dist_km,
            "suggested_price": 7500,
            "suggested_stops": 2,
            "suggested_travel_h": 52,
            "suggested_layover_h": 16,
            "suggested_nights_min": 7,
            "suggested_nights_max": 21,
        }
    if dist_km >= 6500:
        return {
            "dist_km": dist_km,
            "suggested_price": 4500,
            "suggested_stops": 2,
            "suggested_travel_h": 32,
            "suggested_layover_h": 10,
            "suggested_nights_min": 5,
            "suggested_nights_max": 14,
        }
    if dist_km >= 2800:
        return {
            "dist_km": dist_km,
            "suggested_price": 1600,
            "suggested_stops": 1,
            "suggested_travel_h": 16,
            "suggested_layover_h": 6,
            "suggested_nights_min": 3,
            "suggested_nights_max": 8,
        }
    return {
        "dist_km": dist_km,
        "suggested_price": 750,
        "suggested_stops": 1,
        "suggested_travel_h": 9,
        "suggested_layover_h": 4,
        "suggested_nights_min": 2,
        "suggested_nights_max": 6,
    }


def _attach_route_defaults(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for it in items:
        if "suggested_price" not in it:
            it.update(_estimate_defaults_by_codes(it.get("codes", ""), it.get("preset_id", "")))
    return items


def search_world_airports(
    query: str,
    limit: int = 12,
    include_presets: bool = True,
) -> List[Dict[str, Any]]:
    """Fast unified search across Polish presets, multi-airport cities, and 3,100+ active global airports."""
    raw_s = (query or "").strip()
    q = _norm_text(raw_s)
    if not q:
        return []

    results: List[Dict[str, Any]] = []
    seen_codes_keys: set[str] = set()

    # Multi-airport comma/semicolon list (e.g. "Kraków, Katowice" or "KRK, KTW")
    if "," in raw_s or ";" in raw_s:
        parts = [p.strip() for p in re.split(r"[,;]+", raw_s) if p.strip()]
        if len(parts) >= 2:
            combined_codes: List[str] = []
            combined_labels: List[str] = []
            all_pl = True
            for part in parts[:6]:
                sub_hits = search_world_airports(part, limit=2, include_presets=False)
                if not sub_hits and include_presets:
                    sub_hits = search_world_airports(part, limit=1, include_presets=True)
                if not sub_hits:
                    continue
                chosen = sub_hits[0]
                part_codes = [c.strip().upper() for c in (chosen.get("codes") or "").split(",") if len(c.strip()) == 3]
                if not part_codes:
                    continue
                short_name = chosen["label"].split(",")[0].strip()
                for c in part_codes:
                    if c not in combined_codes:
                        combined_codes.append(c)
                    if c not in POLISH_AIRPORTS:
                        all_pl = False
                combined_labels.append(f"{short_name} ({'/'.join(part_codes)})" if f"({part_codes[0]}" not in short_name else short_name)
            if combined_codes:
                code_str = ",".join(combined_codes)
                seen_codes_keys.add(f"codes:{code_str}")
                results.append({
                    "kind": "multi",
                    "type": f"Wiele lotnisk ({len(combined_codes)})",
                    "label": " + ".join(combined_labels),
                    "sub": f"Wybrane kody: {code_str}",
                    "codes": code_str,
                    "preset_id": "PL" if all_pl else "",
                    "cc": "PL" if all_pl else "",
                })
                if len(results) >= limit:
                    return _attach_route_defaults(results[:limit])
                # Also append individual suggestions for the last typed fragment
                last_hits = search_world_airports(parts[-1], limit=max(4, limit - 1), include_presets=include_presets)
                for h in last_hits:
                    k = f"codes:{h.get('codes', '')}"
                    if k not in seen_codes_keys and len(results) < limit:
                        seen_codes_keys.add(k)
                        results.append(h)
                return _attach_route_defaults(results[:limit])
        elif len(parts) == 1:
            return search_world_airports(parts[0], limit=limit, include_presets=include_presets)

    if include_presets:
        for p in WORLD_PRESETS:
            if p["id"] == "EUROPE" or not p.get("codes"):
                continue
            if p["id"] == "WORLD" and q not in ("world", "swiat", "caly swiat"):
                continue
            p_lbl = _norm_text(p["label"])
            p_cds = _norm_text(p["codes"])
            if q in p_lbl or q == p["id"].lower() or q in p_cds:
                key = f"preset:{p['codes']}"
                if key not in seen_codes_keys:
                    seen_codes_keys.add(key)
                    results.append({
                        "kind": "preset",
                        "type": "Kraj / Region",
                        "label": p["label"],
                        "sub": p.get("group") or "Kierunek",
                        "codes": p["codes"],
                        "preset_id": p["id"],
                        "cc": p["id"] if len(p["id"]) == 2 else "",
                    })

        for cs in CITY_SUGGESTIONS:
            if q in _norm_text(cs["label"]) or q in _norm_text(cs["codes"]):
                key = f"codes:{cs['codes']}"
                if key not in seen_codes_keys:
                    seen_codes_keys.add(key)
                    results.append({
                        "kind": "city",
                        "type": "Miasto / Wiele lotnisk",
                        "label": cs["label"],
                        "sub": f"Kod: {cs['codes']}",
                        "codes": cs["codes"],
                        "preset_id": cs.get("preset_id") or "",
                        "cc": "",
                    })

    # Dynamic country grouping from global index (for countries beyond presets, e.g. Wietnam, Kenia, Brazylia)
    if len(q) >= 3:
        country_buckets: Dict[str, List[Dict[str, Any]]] = {}
        for item, _, _, cn_n, _ in _WORLD_SEARCH_ROWS:
            if cn_n and (cn_n.startswith(q) or q in cn_n):
                country_buckets.setdefault(item.get("cn") or item.get("cc") or "", []).append(item)
        for c_name, items in sorted(country_buckets.items(), key=lambda kv: -sum(x.get("p", 0) for x in kv[1]))[:2]:
            top_codes = ",".join(x["c"] for x in items[:8])
            key = f"codes:{top_codes}"
            if len(items) >= 2 and key not in seen_codes_keys and not any(
                r["kind"] == "preset" and _norm_text(r["label"]) == _norm_text(c_name) for r in results
            ):
                seen_codes_keys.add(key)
                cc = (items[0].get("cc") or "").upper()
                results.append({
                    "kind": "country",
                    "type": "Kraj (główne lotniska)",
                    "label": f"{c_name} ({len(items[:8])} głównych lotnisk)",
                    "sub": f"Kody: {top_codes}",
                    "codes": top_codes,
                    "preset_id": "",
                    "cc": cc,
                })

    # Global airport matching across all 3,182 active airports
    scored_airports: List[tuple[tuple[int, int, int], Dict[str, Any]]] = []
    for item, code_l, city_n, cn_n, full_n in _WORLD_SEARCH_ROWS:
        if q not in full_n:
            continue
        exact_code = 1 if code_l == q else 0
        prefix_city = 1 if (city_n.startswith(q) or _norm_text(item.get("a")).startswith(q)) else 0
        prefix_country = 1 if cn_n.startswith(q) else 0
        rank_tuple = (
            exact_code * 10 + prefix_city * 4 + prefix_country * 2,
            1 if code_l.startswith(q) else 0,
            int(item.get("p") or 0),
        )
        scored_airports.append((rank_tuple, item))

    scored_airports.sort(key=lambda x: x[0], reverse=True)
    for _, ap in scored_airports:
        if len(results) >= limit:
            break
        key = f"codes:{ap['c']}"
        if key in seen_codes_keys:
            continue
        seen_codes_keys.add(key)
        city_label = ap.get("city") or ap["c"]
        cn_label = ap.get("cn") or ap.get("cc") or ""
        ap_name = ap.get("n") or city_label
        alias_hint = ""
        if ap.get("a"):
            first_alias = ap["a"].split("|")[0].strip()
            if first_alias and _norm_text(first_alias) != _norm_text(city_label) and len(first_alias) <= 24:
                alias_hint = f" / {first_alias}"
        results.append({
            "kind": "airport",
            "type": f"Lotnisko ({ap['c']})",
            "label": f"{city_label}{alias_hint}, {cn_label} ({ap['c']})" if cn_label else f"{city_label}{alias_hint} ({ap['c']})",
            "sub": ap_name,
            "codes": ap["c"],
            "preset_id": "PL" if ap.get("cc") == "PL" else "",
            "cc": ap.get("cc") or "",
        })

    return _attach_route_defaults(results[:limit])


async def search_world_airports_live(
    query: str,
    limit: int = 12,
    include_presets: bool = True,
) -> List[Dict[str, Any]]:
    local = search_world_airports(query, limit=limit, include_presets=include_presets)
    raw = (query or "").strip()
    norm_q = _norm_text(raw)
    if local or len(norm_q) < 3:
        return local

    if norm_q in _LIVE_PLACES_CACHE:
        return list(_LIVE_PLACES_CACHE[norm_q])[:limit]

    # Live Kiwi GraphQL fallback for ultra-rare airports not in the bundled top 3,182 dump
    gql = """
    query($term: String!) {
      places(search: { term: $term }, filter: { onlyTypes: [AIRPORT, CITY] }, first: 8) {
        ... on PlaceConnection {
          edges {
            node {
              __typename
              name
              ... on Station { code city { name country { name code } } gps { lat lng } }
              ... on City { name code country { name code } }
            }
          }
        }
      }
    }
    """
    live_hits: List[Dict[str, Any]] = []
    seen_live: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                "https://api.skypicker.com/umbrella/v2/graphql",
                json={"query": gql, "variables": {"term": raw}},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code == 200:
                edges = ((resp.json().get("data") or {}).get("places") or {}).get("edges") or []
                for e in edges:
                    node = (e or {}).get("node") or {}
                    code = (node.get("code") or "").strip().upper()
                    if len(code) != 3 or not code.isalpha() or code in seen_live:
                        continue
                    seen_live.add(code)
                    c_obj = node.get("city") or {}
                    country_obj = (c_obj.get("country") or node.get("country") or {})
                    cc = (country_obj.get("code") or "").upper()
                    cn = _COUNTRY_NAME_BY_CC.get(cc) or country_obj.get("name") or cc
                    city_name = c_obj.get("name") or node.get("name") or code
                    gps = node.get("gps") or {}
                    if code not in AIRPORT_COORDS and gps.get("lat") is not None and gps.get("lng") is not None:
                        AIRPORT_COORDS[code] = (float(gps["lat"]), float(gps["lng"]))
                    live_hits.append({
                        "kind": "airport",
                        "type": f"Lotnisko ({code})",
                        "label": f"{city_name}, {cn} ({code})" if cn else f"{city_name} ({code})",
                        "sub": node.get("name") or city_name,
                        "codes": code,
                        "preset_id": "PL" if cc == "PL" else "",
                        "cc": cc,
                    })
    except Exception:
        pass

    if live_hits:
        _attach_route_defaults(live_hits)
        _LIVE_PLACES_CACHE[norm_q] = live_hits
    return _attach_route_defaults(live_hits[:limit])


def estimate_distance_km(origin_iata: str, dest_iata: str) -> Optional[int]:
    c1 = AIRPORT_COORDS.get(origin_iata.upper())
    c2 = AIRPORT_COORDS.get(dest_iata.upper())
    if not c1 or not c2:
        return None
    lat1, lon1 = math.radians(c1[0]), math.radians(c1[1])
    lat2, lon2 = math.radians(c2[0]), math.radians(c2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return int(round(6371.0 * c))


from urllib.parse import quote, quote_plus, parse_qs, urlparse
import httpx

def build_booking_links(
    origin_iata: str,
    dest_iata: str,
    outbound_date: str,
    inbound_date: Optional[str] = None,
    adults: int = 1,
    kiwi_deep_link: Optional[str] = None,
) -> Dict[str, str]:
    out_day = outbound_date[:10]
    in_day = inbound_date[:10] if inbound_date else ""

    if in_day:
        ryanair_url = (
            f"https://www.ryanair.com/pl/pl/trip/flights/select"
            f"?adults={adults}&teens=0&children=0&infants=0"
            f"&dateOut={out_day}&dateIn={in_day}"
            f"&isConnectedFlight=false&discount=0&isReturn=true"
            f"&originIata={origin_iata}&destinationIata={dest_iata}"
        )
        wizzair_url = f"https://www.wizzair.com/pl-pl/loty/{origin_iata.lower()}/{dest_iata.lower()}"
        google_url = f"https://www.google.com/travel/flights?hl=pl&curr=PLN&q=Flights%20from%20{origin_iata}%20to%20{dest_iata}%20on%20{out_day}%20through%20{in_day}"
    else:
        ryanair_url = (
            f"https://www.ryanair.com/pl/pl/trip/flights/select"
            f"?adults={adults}&teens=0&children=0&infants=0"
            f"&dateOut={out_day}"
            f"&isConnectedFlight=false&discount=0&isReturn=false"
            f"&originIata={origin_iata}&destinationIata={dest_iata}"
        )
        wizzair_url = f"https://www.wizzair.com/pl-pl/loty/{origin_iata.lower()}/{dest_iata.lower()}"
        google_url = f"https://www.google.com/travel/flights?hl=pl&curr=PLN&q=One%20way%20flights%20from%20{origin_iata}%20to%20{dest_iata}%20on%20{out_day}"

    if kiwi_deep_link and kiwi_deep_link.startswith("/"):
        kiwi_url = f"https://www.kiwi.com{kiwi_deep_link}"
    elif kiwi_deep_link and kiwi_deep_link.startswith("http"):
        kiwi_url = kiwi_deep_link
    else:
        ret_part = in_day if in_day else "no-return"
        kiwi_url = f"https://www.kiwi.com/pl/search/results/{origin_iata}/{dest_iata}/{out_day}/{ret_part}?adults={adults}"

    return {
        "ryanair": ryanair_url,
        "wizzair": wizzair_url,
        "google_flights": google_url,
        "kiwi": kiwi_url,
    }


_AGODA_PAGE_TYPE_PARAM: Dict[int, str] = {
    5: "city",
    6: "area",
    14: "region",
    3: "country",
}

# Verified Agoda destination IDs for instant pre-filled search links; unknown cities resolve via /go/agoda
_AGODA_DEST_CACHE: Dict[str, tuple[str, str]] = {
    "mediolan": ("city", "16585"),
    "milan": ("city", "16585"),
    "rzym": ("city", "16594"),
    "rome": ("city", "16594"),
    "wenecja": ("city", "17164"),
    "venice": ("city", "17164"),
    "bari": ("city", "4094"),
    "neapol": ("city", "16572"),
    "naples": ("city", "16572"),
    "katania": ("city", "5596"),
    "catania": ("city", "5596"),
    "palermo": ("city", "14810"),
    "bolonia": ("city", "1002"),
    "bologna": ("city", "1002"),
    "piza": ("city", "13723"),
    "pisa": ("city", "13723"),
    "barcelona": ("city", "2002"),
    "madryt": ("city", "5531"),
    "madrid": ("city", "5531"),
    "malaga": ("city", "12521"),
    "alicante": ("city", "14186"),
    "palma de mallorca": ("area", "30752"),
    "majorka": ("area", "30752"),
    "walencja": ("city", "1623"),
    "valencia": ("city", "1623"),
    "sewilla": ("city", "16577"),
    "seville": ("city", "16577"),
    "ibiza": ("city", "2976"),
    "teneryfa": ("city", "17379"),
    "tenerife": ("city", "17379"),
    "las palmas de gran canaria": ("city", "18008"),
    "arrecife": ("area", "33266"),
    "funchal": ("city", "12453"),
    "lizbona": ("city", "16364"),
    "lisbon": ("city", "16364"),
    "porto": ("city", "8738"),
    "faro": ("city", "5590"),
    "ateny": ("city", "16571"),
    "athens": ("city", "16571"),
    "saloniki": ("city", "8711"),
    "chania": ("city", "162010"),
    "heraklion": ("area", "34030"),
    "rodos": ("city", "16051"),
    "rhodes": ("city", "16051"),
    "korfu": ("city", "18670"),
    "corfu": ("city", "18670"),
    "santorini": ("city", "16084"),
    "pafos": ("city", "517"),
    "paphos": ("city", "517"),
    "larnaka": ("city", "2993"),
    "larnaca": ("city", "2993"),
    "cypr": ("city", "517"),
    "malta": ("city", "5923"),
    "zadar": ("city", "72200"),
    "split": ("city", "6325"),
    "dubrownik": ("city", "4839"),
    "dubrovnik": ("city", "4839"),
    "tirana": ("city", "9206"),
    "londyn": ("city", "233"),
    "london": ("city", "233"),
    "edynburg": ("city", "18924"),
    "edinburgh": ("city", "18924"),
    "dublin": ("city", "1131"),
    "paryż": ("city", "15470"),
    "paris": ("city", "15470"),
    "nicea": ("city", "5277"),
    "nice": ("city", "5277"),
    "amsterdam": ("city", "13868"),
    "eindhoven": ("city", "2316"),
    "bruksela": ("city", "15904"),
    "brussels": ("city", "15904"),
    "wiedeń": ("city", "16582"),
    "vienna": ("city", "16582"),
    "praga": ("city", "15977"),
    "prague": ("city", "15977"),
    "budapeszt": ("city", "10647"),
    "budapest": ("city", "10647"),
    "bratysława": ("city", "4091"),
    "bratislava": ("city", "4091"),
    "kopenhaga": ("city", "16480"),
    "copenhagen": ("city", "16480"),
    "billund": ("city", "15552"),
    "sztokholm": ("city", "1618"),
    "stockholm": ("city", "1618"),
    "oslo": ("city", "12746"),
    "bergen": ("city", "14414"),
    "reykjavik": ("city", "13864"),
    "antalya": ("city", "7493"),
    "stambuł": ("city", "14932"),
    "istanbul": ("city", "14932"),
    "dubaj": ("city", "2994"),
    "dubai": ("city", "2994"),
    "abu zabi": ("city", "10182"),
    "abu dhabi": ("city", "10182"),
    "marrakesz": ("city", "11825"),
    "marrakesh": ("city", "11825"),
    "agadir": ("city", "10475"),
    "hurghada": ("city", "6700"),
    "sharm el sheikh": ("city", "15897"),
    "nowy jork": ("city", "318"),
    "new york": ("city", "318"),
    "miami": ("city", "18720"),
    "bangkok": ("city", "9395"),
    "phuket": ("city", "16056"),
    "singapur": ("city", "4064"),
    "singapore": ("city", "4064"),
    "kuala lumpur": ("city", "14524"),
    "bali": ("city", "17193"),
    "tokio": ("city", "5085"),
    "tokyo": ("city", "5085"),
    "warszawa": ("city", "16587"),
    "warsaw": ("city", "16587"),
    "kraków": ("city", "15790"),
    "krakow": ("city", "15790"),
    "gdańsk": ("city", "3935"),
    "gdansk": ("city", "3935"),
    "wrocław": ("city", "3339"),
    "wroclaw": ("city", "3339"),
    "poznań": ("city", "664"),
    "poznan": ("city", "664"),
    "katowice": ("city", "2210"),
    "szczecin": ("city", "1495"),
    "rzeszów": ("city", "19572"),
    "rzeszow": ("city", "19572"),
    "bydgoszcz": ("city", "13481"),
    "lublin": ("city", "14968"),
    "olsztyn": ("city", "4248"),
    "zielona góra": ("city", "2"),
    "zielona gora": ("city", "2"),
}


def _build_agoda_search_url(
    target: tuple[str, str], c_in: str, c_out: str, pax: int
) -> str:
    param_key, param_val = target
    url = f"https://www.agoda.com/pl-pl/search?{param_key}={quote_plus(str(param_val))}"
    if c_in:
        url += f"&checkIn={quote_plus(c_in)}"
    if c_out:
        url += f"&checkOut={quote_plus(c_out)}"
    return f"{url}&adults={pax}&rooms=1&currency=PLN"


def _pick_agoda_target(items: List[Dict[str, Any]]) -> Optional[tuple[str, str]]:
    """Prefer city/area results over individual hotel/landmark POIs."""
    for it in items:
        ru = it.get("ResultUrl") or ""
        qs = parse_qs(urlparse(ru).query)
        for key in ("city", "area"):
            if qs.get(key) and qs[key][0]:
                return (key, qs[key][0])

    for it in items:
        cid = it.get("CityId") or 0
        if isinstance(cid, int) and cid > 0:
            return ("city", str(cid))

    for it in items:
        ru = it.get("ResultUrl") or ""
        qs = parse_qs(urlparse(ru).query)
        for key in ("region", "country"):
            if qs.get(key) and qs[key][0]:
                return (key, qs[key][0])
        pt = _AGODA_PAGE_TYPE_PARAM.get(it.get("PageTypeId"))
        oid = it.get("ObjectId") or 0
        if pt and oid:
            return (pt, str(oid))
    return None


async def resolve_agoda_search_url(
    city_name: str,
    checkin_date: str = "",
    checkout_date: str = "",
    adults: int = 2,
) -> str:
    clean_city = (city_name or "").strip() or "Europe"
    c_in = (checkin_date or "")[:10]
    c_out = (checkout_date or "")[:10]
    pax = max(1, min(20, int(adults or 2)))

    key = clean_city.lower()
    cached = _AGODA_DEST_CACHE.get(key)
    if cached:
        return _build_agoda_search_url(cached, c_in, c_out, pax)

    try:
        suggest_url = (
            "https://www.agoda.com/api/cronos/search/GetUnifiedSuggestResult"
            f"/3/20/20/0/pl-pl/?searchText={quote(clean_city)}"
        )
        async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as client:
            resp = await client.get(suggest_url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                items = (resp.json() or {}).get("ViewModelList") or []
                target = _pick_agoda_target(items)
                if target:
                    _AGODA_DEST_CACHE[key] = target
                    return _build_agoda_search_url(target, c_in, c_out, pax)
    except Exception:
        pass

    return "https://www.agoda.com/pl-pl/"


def build_lodging_links(
    city_name: str,
    checkin_date: str,
    checkout_date: str,
    adults: int = 2,
) -> Dict[str, str]:
    clean_city = (city_name or "").strip() or "Europe"
    c_in = (checkin_date or "")[:10]
    c_out = (checkout_date or "")[:10]
    pax = max(1, int(adults or 1))
    enc_city = quote_plus(clean_city)

    booking_url = (
        f"https://www.booking.com/searchresults.pl.html"
        f"?ss={enc_city}&checkin={c_in}&checkout={c_out}&group_adults={pax}&no_rooms=1"
    )
    airbnb_url = (
        f"https://www.airbnb.pl/s/{enc_city}/homes"
        f"?checkin={c_in}&checkout={c_out}&adults={pax}"
    )
    agoda_target = _AGODA_DEST_CACHE.get(clean_city.lower())
    if agoda_target:
        agoda_url = _build_agoda_search_url(agoda_target, c_in, c_out, pax)
    else:
        agoda_url = (
            f"/go/agoda?city={enc_city}&checkin={quote_plus(c_in)}"
            f"&checkout={quote_plus(c_out)}&adults={pax}"
        )
    return {
        "booking": booking_url,
        "airbnb": airbnb_url,
        "agoda": agoda_url,
    }
