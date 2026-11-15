"""English and Chinese names for the built-in fields, choices, masks, tabs and sections.

They are filled in when a school is set up, and by a migration for existing schools, but only where
the German name is still the one the app shipped: a name a school changed stays as it is.
"""

NAMES = {
    # Fields
    "Vorname": ("First name", "名", "名"),
    "Nachname": ("Last name", "姓", "姓"),
    "Familienname zuerst": ("Family name first", "姓在前", "姓在前"),
    "Geschlecht": ("Gender", "性别", "性別"),
    "Geburtsdatum": ("Date of birth", "出生日期", "出生日期"),
    "E-Mail": ("Email", "电子邮箱", "電子郵件"),
    "Eintritt": ("Entry date", "入校日期", "入校日期"),
    "Austritt": ("Exit date", "离校日期", "離校日期"),
    "Bemerkungen": ("Notes", "备注", "備註"),
    "Archiviert": ("Archived", "已归档", "已歸檔"),
    "Klasse": ("Class", "班级", "班級"),
    "Schultyp": ("School type", "学校类型", "學校類型"),
    "Klassenstufe": ("Grade", "年级", "年級"),
    "Geburtsort": ("Place of birth", "出生地", "出生地"),
    "Lese-/Rechtschreibschwäche": ("Dyslexia", "读写障碍", "讀寫障礙"),
    "Bus": ("School bus", "校车", "校車"),
    "Neue Codes/Accounts": ("New codes/accounts", "需要新代码/账号", "需要新代碼/帳號"),
    "Kategorie": ("Category", "类别", "類別"),
    "DaZ-Fachkraft": ("DaZ teacher", "DaZ 教师", "DaZ 教師"),
    "Kindergarten-Fachkraft": ("Kindergarten educator", "幼儿园教师", "幼兒園教師"),
    "Anrede": ("Salutation", "称呼", "稱呼"),
    "Kürzel": ("Initials", "缩写", "縮寫"),
    "Mobiltelefon": ("Mobile phone", "手机", "手機"),
    "Tel. privat": ("Phone (private)", "私人电话", "私人電話"),
    "Tel. Büro": ("Phone (office)", "办公电话", "辦公電話"),
    "Tel. Notruf": ("Emergency phone", "紧急电话", "緊急電話"),
    "E-Mail privat": ("Private email", "私人邮箱", "私人電子郵件"),
    "Compound": ("Compound", "小区", "社區"),
    "Straße / Nr.": ("Street / No.", "街道 / 门牌号", "街道 / 門牌號"),
    "Bezirk": ("District", "区", "區"),
    "Stadt / PLZ": ("City / Postcode", "城市 / 邮编", "城市 / 郵遞區號"),
    "Straßenbüro": ("Street office", "街道办事处", "街道辦事處"),
    # Choices
    "Weiblich": ("Female", "女", "女"),
    "Männlich": ("Male", "男", "男"),
    "Divers": ("Diverse", "其他", "其他"),
    "Frau": ("Ms", "女士", "女士"),
    "Herr": ("Mr", "先生", "先生"),
    "Schulleitung": ("School management", "校领导", "校領導"),
    "Lehrpersonal": ("Teaching staff", "教师", "教師"),
    "Verwaltung": ("Administration", "行政", "行政"),
    "Kindergarten": ("Kindergarten", "幼儿园", "幼兒園"),
    "Technik": ("Technical staff", "技术人员", "技術人員"),
    "Praktikant": ("Intern", "实习生", "實習生"),
    "Besucher": ("Visitor", "访客", "訪客"),
    "Gast": ("Guest", "访客", "訪客"),
    # Masks
    "Schüler": ("Pupils", "学生", "學生"),
    "Personal": ("Staff", "教职工", "教職員"),
    # Tabs and sections
    "Stammdaten": ("Basic data", "基本信息", "基本資料"),
    "Grunddaten": ("Basic data", "基本信息", "基本資料"),
    "Allgemeine Daten": ("General", "一般信息", "一般資料"),
    "Foto und Karte": ("Photo and card", "照片和证件卡", "照片和證件卡"),
    "IT Informationen": ("IT information", "IT 信息", "IT 資訊"),
    "Konto": ("Account", "账号", "帳號"),
    "Geräte": ("Devices", "设备", "設備"),
    "Bisherige Supportfälle": ("Past support cases", "以往的支持记录", "以往的支援紀錄"),
    "eBooks & Passwörter": ("eBooks & passwords", "电子书和密码", "電子書和密碼"),
    "Eltern": ("Parents", "家长", "家長"),
    "Klasse & Verlauf": ("Class & history", "班级和历史", "班級和歷史"),
    "Dateien & Notizen": ("Files & notes", "文件和备注", "檔案和備註"),
    "Anschrift in China": ("Address in China", "中国地址", "中國地址"),
    "Surface oder iPad": ("Surface or iPad", "Surface 或 iPad", "Surface 或 iPad"),
    "Zugänge": ("Logins", "登录信息", "登入資訊"),
}

LANGUAGES = ("en", "zh-hans", "zh-hant")


def names_for(german):
    """{"en": …, "zh-hans": …, "zh-hant": …} for a shipped German name, else {}."""
    found = NAMES.get(german)
    return dict(zip(LANGUAGES, found)) if found else {}


def fill_options(options):
    out = []
    for option in options or []:
        option = dict(option)
        if not option.get("labels") and not option.get("label"):
            names = names_for(option.get("value", ""))
            if names:
                option["labels"] = names
        out.append(option)
    return out


def fill_tree(tree):
    """Add the shipped names to tabs and sections that still carry a shipped German title."""
    changed = False
    for tab in tree.get("tabs", []):
        for node in [tab, *tab.get("sections", [])]:
            if not node.get("titles"):
                names = names_for(node.get("title", ""))
                if names:
                    node["titles"] = names
                    changed = True
    return changed
