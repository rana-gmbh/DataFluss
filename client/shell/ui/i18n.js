// The start screen's languages: German (as written in index.html and launcher.js), English, Simplified and
// Traditional Chinese, as in the databases (locale/). It starts in the system's language (English for one
// it does not offer); a choice made with the switcher is remembered on this computer.
window.I18N = (() => {
  const LANGUAGES = [["de", "Deutsch"], ["en", "English"], ["zh-hans", "简体中文"], ["zh-hant", "繁體中文"]];
  const KEY = "datafluss-language";

  function fromSystem() {
    for (const tag of navigator.languages?.length ? navigator.languages : [navigator.language || "en"]) {
      const code = tag.toLowerCase();
      if (code.startsWith("de")) return "de";
      if (code.startsWith("zh")) return /hant|-tw|-hk|-mo/.test(code) ? "zh-hant" : "zh-hans";
      if (code.startsWith("en")) return "en";
    }
    return "en";
  }

  let chosen = null;
  try { chosen = localStorage.getItem(KEY); } catch {}
  const language = LANGUAGES.some(([code]) => code === chosen) ? chosen : fromSystem();
  document.documentElement.lang = language;

  // German text -> [English, Simplified Chinese, Traditional Chinese]. {name} stands for a value.
  const T = {
    "Eigene Datenbanken, verschlüsselt, auf Wunsch mit einem Server abgeglichen": ["Your own databases, encrypted, synced with a server if you wish", "自己的数据库，已加密，可选择与服务器同步", "自己的資料庫，已加密，可選擇與伺服器同步"],
    "Neue Datenbank": ["New database", "新建数据库", "新增資料庫"],
    "Mit einem Ausgangspunkt wie der Schulverwaltung, auf diesem Mac": ["Starting from a module such as school administration, on this Mac", "从学校管理等起点开始，在这台 Mac 上", "從學校管理等起點開始，在這台 Mac 上"],
    "Mit einem Ausgangspunkt wie der Schulverwaltung, auf diesem PC": ["Starting from a module such as school administration, on this PC", "从学校管理等起点开始，在这台电脑上", "從學校管理等起點開始，在這台電腦上"],
    "Mit Server verbinden": ["Connect to server", "连接服务器", "連線伺服器"],
    "Eine Kopie, die sich mit dem Server abgleicht": ["A copy that syncs with the server", "与服务器同步的副本", "與伺服器同步的副本"],
    "Sicherung importieren": ["Import backup", "导入备份", "匯入備份"],
    "Von einem Server oder einem anderen Mac": ["From a server or another Mac", "来自服务器或另一台 Mac", "來自伺服器或另一台 Mac"],
    "Von einem Server oder einem anderen Computer": ["From a server or another computer", "来自服务器或另一台电脑", "來自伺服器或另一台電腦"],
    "Mit erfundenen Daten ausprobieren": ["Try it with invented data", "用虚构数据试用", "用虛構資料試用"],
    "Modul installieren …": ["Install module …", "安装模块…", "安裝模組…"],
    "Jede Datenbank ist verschlüsselt. Die Schlüssel liegen im Schlüsselbund dieses Macs.": ["Every database is encrypted. The keys are kept in this Mac's keychain.", "每个数据库都已加密。密钥保存在这台 Mac 的钥匙串中。", "每個資料庫都已加密。金鑰保存在這台 Mac 的鑰匙圈中。"],
    "Jede Datenbank ist verschlüsselt. Die Schlüssel liegen in der Windows-Anmeldeinformationsverwaltung dieses PCs.": ["Every database is encrypted. The keys are kept in this PC's Windows Credential Manager.", "每个数据库都已加密。密钥保存在这台电脑的 Windows 凭据管理器中。", "每個資料庫都已加密。金鑰保存在這台電腦的 Windows 認證管理員中。"],
    "Datenbanken": ["Databases", "数据库", "資料庫"],
    "Datenbanken auf diesem Mac": ["Databases on this Mac", "这台 Mac 上的数据库", "這台 Mac 上的資料庫"],
    "Datenbanken auf diesem PC": ["Databases on this PC", "这台电脑上的数据库", "這台電腦上的資料庫"],
    "Noch keine Datenbank": ["No database yet", "还没有数据库", "還沒有資料庫"],
    "Links eine neue anlegen, eine Sicherung importieren oder mit erfundenen Daten ausprobieren.": ["Create a new one on the left, import a backup or try it with invented data.", "请在左侧新建、导入备份，或用虚构数据试用。", "請在左側新增、匯入備份，或用虛構資料試用。"],
    "Einen Moment …": ["One moment …", "请稍候…", "請稍候…"],
    "Sprache": ["Language", "语言", "語言"],
    // the new-database wizard
    "Womit soll die neue Datenbank beginnen?": ["What should the new database start with?", "新数据库从什么开始？", "新資料庫從什麼開始？"],
    "Ausgangspunkt": ["Starting point", "起点", "起點"],
    "Schulverwaltung": ["School administration", "学校管理", "學校管理"],
    "Schüler, Personal, Eltern und Klassen, Geräte, Schäden und Zugänge, mit Druckvorlagen, Import und Export.": ["Pupils, staff, parents and classes, devices, repairs and logins, with print templates, import and export.", "学生、教职工、家长和班级，设备、损坏和账号，附打印模板、导入和导出。", "學生、教職員、家長和班級，設備、損壞和帳號，附列印範本、匯入和匯出。"],
    "Leer": ["Empty", "空白", "空白"],
    "Nur eigene Tabellen, ohne Personen und Geräte. Felder, Seiten und Druckvorlagen gestalten Sie selbst oder beginnen mit einer Vorlage.": ["Your own tables only, without people and devices. Design the fields, pages and print templates yourself or start from a template.", "只有自定义表格，没有人员和设备。字段、页面和打印模板可自行设计，或从模板开始。", "只有自訂表格，沒有人員和設備。欄位、頁面和列印範本可自行設計，或從範本開始。"],
    "Vorlagen": ["Templates", "模板", "範本"],
    "(fertige Tabellen, später anpassbar)": ["(ready-made tables, can be changed later)", "（现成的表格，之后可修改）", "（現成的表格，之後可修改）"],
    "Mit Beispieldaten": ["With sample data", "包含示例数据", "包含範例資料"],
    "Erfundene Personen und Geräte zum Ausprobieren, dazu ein Konto für jede Rolle, ohne Zwei-Faktor-Anmeldung. Nicht für echte Daten.": ["Invented people and devices to try things out, with an account for every role, without two-factor sign-in. Not for real data.", "用于试用的虚构人员和设备，每个角色一个账号，无需双重验证。不可用于真实数据。", "用於試用的虛構人員和設備，每個角色一個帳號，無需雙重驗證。不可用於真實資料。"],
    "Eine Datenbank mit erfundenen Personen. Die Anmeldedaten erscheinen gleich danach.": ["A database with invented people. The sign-in details appear right afterwards.", "一个包含虚构人员的数据库。登录信息随后显示。", "一個包含虛構人員的資料庫。登入資訊隨後顯示。"],
    "Name der Datenbank": ["Name of the database", "数据库名称", "資料庫名稱"],
    "(für diese Liste)": ["(for this list)", "（用于此列表）", "（用於此清單）"],
    "Noch ohne Personen, mit dem ersten IT-Konto. Beim ersten Anmelden wird die Zwei-Faktor-Anmeldung eingerichtet.": ["No people yet, with the first IT account. Two-factor sign-in is set up at the first sign-in.", "尚无人员，附第一个 IT 账号。首次登录时设置双重验证。", "尚無人員，附第一個 IT 帳號。首次登入時設定雙重驗證。"],
    "Noch ohne Tabellen, mit dem ersten IT-Konto. Beim ersten Anmelden wird die Zwei-Faktor-Anmeldung eingerichtet.": ["No tables yet, with the first IT account. Two-factor sign-in is set up at the first sign-in.", "尚无表格，附第一个 IT 账号。首次登录时设置双重验证。", "尚無表格，附第一個 IT 帳號。首次登入時設定雙重驗證。"],
    "Name der Schule": ["Name of the school", "学校名称", "學校名稱"],
    "Mit den Tabellen der Vorlage und dem ersten IT-Konto. Beim ersten Anmelden wird die Zwei-Faktor-Anmeldung eingerichtet.": ["With the template's tables and the first IT account. Two-factor sign-in is set up at the first sign-in.", "含模板的表格和第一个 IT 账号。首次登录时设置双重验证。", "含範本的表格和第一個 IT 帳號。首次登入時設定雙重驗證。"],
    "Eine Datenbank mit den Beispieldaten der Vorlage. Die Anmeldedaten erscheinen gleich danach.": ["A database with the template's sample data. The sign-in details appear right afterwards.", "含模板示例数据的数据库。登录信息随后显示。", "含範本範例資料的資料庫。登入資訊隨後顯示。"],
    "Name (Schule, Verein, Firma …)": ["Name (school, club, company …)", "名称（学校、协会、公司…）", "名稱（學校、協會、公司…）"],
    "Deutsche Schule Musterstadt": ["Example School", "示例学校", "範例學校"],
    "z. B. Bibliothek am Markt": ["e.g. Market Street Library", "例如：市场街图书馆", "例如：市場街圖書館"],
    "wie die Schule": ["like the school", "与学校相同", "與學校相同"],
    "wie oben oder eigener Name": ["like above, or a name of your own", "与上面相同或自定义名称", "與上面相同或自訂名稱"],
    "Bitte einen Namen und einen Benutzernamen angeben.": ["Please enter a name and a username.", "请输入名称和用户名。", "請輸入名稱和使用者名稱。"],
    "Benutzername": ["Username", "用户名", "使用者名稱"],
    "z. B. it": ["e.g. it", "例如 it", "例如 it"],
    "Passwort": ["Password", "密码", "密碼"],
    "Wiederholen": ["Repeat", "重复", "重複"],
    "Mindestens 12 Zeichen. Ohne dieses Passwort kommt niemand mehr an die Daten.": ["At least 12 characters. Without this password nobody can get at the data any more.", "至少 12 个字符。没有此密码，任何人都无法再访问数据。", "至少 12 個字元。沒有此密碼，任何人都無法再存取資料。"],
    "Mit dem eigenen Konto der Webversion. Auf diesem Mac landen die Daten, die dieses Konto sehen darf, und sie gleichen sich mit dem Server ab, auch nach einer Zeit ohne Verbindung.": ["With your own account of the web version. This Mac receives the data this account may see, and it syncs with the server, also after a time without a connection.", "使用网页版的个人账号。这台 Mac 会获得该账号可查看的数据，并与服务器同步，断线一段时间后也可以。", "使用網頁版的個人帳號。這台 Mac 會取得該帳號可檢視的資料，並與伺服器同步，離線一段時間後也可以。"],
    "Mit dem eigenen Konto der Webversion. Auf diesem PC landen die Daten, die dieses Konto sehen darf, und sie gleichen sich mit dem Server ab, auch nach einer Zeit ohne Verbindung.": ["With your own account of the web version. This PC receives the data this account may see, and it syncs with the server, also after a time without a connection.", "使用网页版的个人账号。这台电脑会获得该账号可查看的数据，并与服务器同步，断线一段时间后也可以。", "使用網頁版的個人帳號。這台電腦會取得該帳號可檢視的資料，並與伺服器同步，離線一段時間後也可以。"],
    "Adresse des Servers": ["Server address", "服务器地址", "伺服器位址"],
    "Code aus der Authenticator-App": ["Code from the authenticator app", "验证器应用中的验证码", "驗證器 App 中的驗證碼"],
    "(falls eingerichtet)": ["(if set up)", "（如已设置）", "（如已設定）"],
    "Die Sicherung wird zu einer neuen Datenbank auf diesem Mac. Es gelten die Konten und Passwörter aus der Sicherung.": ["The backup becomes a new database on this Mac. The accounts and passwords of the backup apply.", "该备份将成为这台 Mac 上的新数据库。沿用备份中的账号和密码。", "該備份將成為這台 Mac 上的新資料庫。沿用備份中的帳號和密碼。"],
    "Die Sicherung wird zu einer neuen Datenbank auf diesem PC. Es gelten die Konten und Passwörter aus der Sicherung.": ["The backup becomes a new database on this PC. The accounts and passwords of the backup apply.", "该备份将成为这台电脑上的新数据库。沿用备份中的账号和密码。", "該備份將成為這台電腦上的新資料庫。沿用備份中的帳號和密碼。"],
    "Datei wählen …": ["Choose file …", "选择文件…", "選擇檔案…"],
    "Keine Datei gewählt (.svbackup)": ["No file chosen (.svbackup)", "未选择文件（.svbackup）", "未選擇檔案（.svbackup）"],
    "Passwort der Sicherung": ["Password of the backup", "备份密码", "備份密碼"],
    "Zugangsdaten mit übernehmen": ["Keep the logins", "一并保留账号信息", "一併保留帳號資訊"],
    "Ohne Haken werden die gespeicherten Passwörter der Plattformen und die Produktschlüssel entfernt, so dass ein verlorener Mac keine Passwörter preisgibt.": ["Without the tick, the stored platform passwords and product keys are removed, so a lost Mac gives away no passwords.", "不勾选时，将删除已保存的平台密码和产品密钥，这样丢失的 Mac 不会泄露密码。", "不勾選時，將刪除已儲存的平台密碼和產品金鑰，這樣遺失的 Mac 不會洩露密碼。"],
    "Ohne Haken werden die gespeicherten Passwörter der Plattformen und die Produktschlüssel entfernt, so dass ein verlorener PC keine Passwörter preisgibt.": ["Without the tick, the stored platform passwords and product keys are removed, so a lost PC gives away no passwords.", "不勾选时，将删除已保存的平台密码和产品密钥，这样丢失的电脑不会泄露密码。", "不勾選時，將刪除已儲存的平台密碼和產品金鑰，這樣遺失的電腦不會洩露密碼。"],
    // the list
    "noch nie geöffnet": ["never opened", "从未打开", "從未開啟"],
    "heute geöffnet": ["opened today", "今天打开过", "今天開啟過"],
    "gestern geöffnet": ["opened yesterday", "昨天打开过", "昨天開啟過"],
    "vor {days} Tagen geöffnet": ["opened {days} days ago", "{days} 天前打开过", "{days} 天前開啟過"],
    "zuletzt {date}": ["last {date}", "上次 {date}", "上次 {date}"],
    "Keine Sicherung eingerichtet": ["No backup set up", "未设置备份", "未設定備份"],
    "Letzte Sicherung fehlgeschlagen": ["Last backup failed", "上次备份失败", "上次備份失敗"],
    "Sicherung seit {days} Tagen überfällig": ["Backup overdue for {days} days", "备份已逾期 {days} 天", "備份已逾期 {days} 天"],
    "{n} auf diesem Mac": ["{n} on this Mac", "这台 Mac 上 {n} 个", "這台 Mac 上 {n} 個"],
    "{n} auf diesem PC": ["{n} on this PC", "这台电脑上 {n} 个", "這台電腦上 {n} 個"],
    "Geöffnet": ["Open", "已打开", "已開啟"],
    "Lokal": ["Local", "本地", "本機"],
    "Die regelmäßige Sicherung lässt sich in der Datenbank unter Verwaltung → Sicherung einrichten.": ["Set up the regular backup in the database under Administration → Backup.", "可在数据库的“管理 → 备份”中设置定期备份。", "可在資料庫的「管理 → 備份」中設定定期備份。"],
    "Anzeigen": ["Show", "显示", "顯示"],
    "Öffnen": ["Open", "打开", "開啟"],
    "Mehr zu {name}": ["More about {name}", "关于 {name} 的更多操作", "關於 {name} 的更多操作"],
    "Im {app} zeigen": ["Show in {app}", "在 {app} 中显示", "在 {app} 中顯示"],
    "Anmeldedaten zeigen …": ["Show sign-in details …", "显示登录信息…", "顯示登入資訊…"],
    "Gemerkte Anmeldung vergessen": ["Forget remembered sign-in", "忘记已记住的登录", "忘記已記住的登入"],
    "In den Papierkorb legen …": ["Move to bin …", "移到废纸篓…", "移到垃圾桶…"],
    // buttons, messages and busy texts
    "Abbrechen": ["Cancel", "取消", "取消"],
    "Weiter": ["Next", "下一步", "下一步"],
    "Zurück": ["Back", "返回", "返回"],
    "Anlegen": ["Create", "创建", "建立"],
    "Neue Datenbank mit Beispieldaten": ["New database with sample data", "新建含示例数据的数据库", "新增含範例資料的資料庫"],
    "Bitte den Namen der Schule und einen Benutzernamen angeben.": ["Please enter the name of the school and a username.", "请输入学校名称和用户名。", "請輸入學校名稱和使用者名稱。"],
    "Das Passwort braucht mindestens 12 Zeichen.": ["The password needs at least 12 characters.", "密码至少需要 12 个字符。", "密碼至少需要 12 個字元。"],
    "Die beiden Passwörter stimmen nicht überein.": ["The two passwords do not match.", "两次输入的密码不一致。", "兩次輸入的密碼不一致。"],
    "Datenbank wird angelegt …": ["Creating database …", "正在创建数据库…", "正在建立資料庫…"],
    "Verbinden": ["Connect", "连接", "連線"],
    "Bitte Adresse, Benutzername und Passwort angeben.": ["Please enter address, username and password.", "请输入地址、用户名和密码。", "請輸入位址、使用者名稱和密碼。"],
    "Verbindung zum Server …": ["Connecting to the server …", "正在连接服务器…", "正在連線伺服器…"],
    "Importieren": ["Import", "导入", "匯入"],
    "Bitte zuerst die Sicherungsdatei wählen.": ["Please choose the backup file first.", "请先选择备份文件。", "請先選擇備份檔案。"],
    "Bitte das Passwort der Sicherung eingeben.": ["Please enter the password of the backup.", "请输入备份密码。", "請輸入備份密碼。"],
    "Bitte der Datenbank einen Namen geben.": ["Please give the database a name.", "请为数据库命名。", "請為資料庫命名。"],
    "Sicherung wird geprüft …": ["Checking backup …", "正在检查备份…", "正在檢查備份…"],
    "Demo anlegen": ["Create demo", "创建演示", "建立示範"],
    "Die Schulverwaltung mit rund 600 erfundenen Personen, Geräten und Zugängen, zum Ausprobieren. Ein Konto für jede Rolle, ohne Zwei-Faktor-Anmeldung. Keine echten Daten eintragen.": ["School administration with about 600 invented people, devices and logins, to try things out. An account for every role, without two-factor sign-in. Do not enter real data.", "学校管理，含约 600 个虚构人员、设备和账号，供试用。每个角色一个账号，无需双重验证。请勿输入真实数据。", "學校管理，含約 600 個虛構人員、設備和帳號，供試用。每個角色一個帳號，無需雙重驗證。請勿輸入真實資料。"],
    "Demo wird angelegt …": ["Creating demo …", "正在创建演示…", "正在建立示範…"],
    "Anmeldedaten": ["Sign-in details", "登录信息", "登入資訊"],
    "Schließen": ["Close", "关闭", "關閉"],
    "Sekretariat": ["Office", "秘书处", "秘書處"],
    "Schulleitung": ["School management", "校领导", "校方管理層"],
    "DaZ-Lehrkraft": ["German as a second language teacher", "德语作为第二语言教师", "德語作為第二語言教師"],
    "Kindergarten": ["Kindergarten", "幼儿园", "幼兒園"],
    "Lehrkraft": ["Teacher", "教师", "教師"],
    "Mit diesen Konten anmelden. Sie stehen auch im Ordner der Datenbank.": ["Sign in with these accounts. They are also in the database's folder.", "请使用这些账号登录。它们也保存在数据库文件夹中。", "請使用這些帳號登入。它們也保存在資料庫資料夾中。"],
    "Demo-Anmeldedaten": ["Demo sign-in details", "演示登录信息", "示範登入資訊"],
    "Demo öffnen": ["Open demo", "打开演示", "開啟示範"],
    "„{name}“ wird mit allen Daten in den Papierkorb gelegt.": ["“{name}” is moved to the bin with all its data.", "“{name}”将连同所有数据移到废纸篓。", "「{name}」將連同所有資料移到垃圾桶。"],
    "Ihr Fenster wird geschlossen.": ["Its window is closed.", "其窗口将被关闭。", "其視窗將被關閉。"],
    "Solange der Ordner im Papierkorb liegt, lässt er sich zurücklegen.": ["While the folder is in the bin, it can be put back.", "只要文件夹还在废纸篓中，就可以放回。", "只要資料夾還在垃圾桶中，就可以放回。"],
    "In den Papierkorb legen?": ["Move to bin?", "移到废纸篓？", "移到垃圾桶？"],
    "In den Papierkorb": ["Move to bin", "移到废纸篓", "移到垃圾桶"],
    "Datenbank": ["Database", "数据库", "資料庫"],
    "Datenbank wird geöffnet …": ["Opening database …", "正在打开数据库…", "正在開啟資料庫…"],
    "{name} liegt bereit. DataFluss prüft die Signatur beim nächsten Öffnen einer Datenbank und lädt das Modul nur, wenn sie stimmt und das Modul zu dieser Version passt.": ["{name} is ready. DataFluss checks its signature the next time a database opens, and loads the module only if the signature is valid and the module fits this version.", "{name} 已就绪。DataFluss 会在下次打开数据库时检查签名，仅在签名有效且模块与此版本相符时加载。", "{name} 已就緒。DataFluss 會在下次開啟資料庫時檢查簽章，僅在簽章有效且模組與此版本相符時載入。"],
    "Modul installiert": ["Module installed", "模块已安装", "模組已安裝"],
    "Modul installieren": ["Install module", "安装模块", "安裝模組"],
    "OK": ["OK", "确定", "確定"],
    // welcome on the first start
    "Willkommen bei DataFluss": ["Welcome to DataFluss", "欢迎使用 DataFluss", "歡迎使用 DataFluss"],
    "DataFluss hält Daten in eigenen Datenbanken: auf diesem Mac, verschlüsselt, und auf Wunsch im Abgleich mit einem Server, auch nach einer Zeit ohne Verbindung.": ["DataFluss keeps data in databases of your own: on this Mac, encrypted, and synced with a server if you wish, also after a time without a connection.", "DataFluss 将数据保存在你自己的数据库中：在这台 Mac 上，已加密，并可选择与服务器同步，断线一段时间后也可以。", "DataFluss 將資料保存在你自己的資料庫中：在這台 Mac 上，已加密，並可選擇與伺服器同步，離線一段時間後也可以。"],
    "DataFluss hält Daten in eigenen Datenbanken: auf diesem PC, verschlüsselt, und auf Wunsch im Abgleich mit einem Server, auch nach einer Zeit ohne Verbindung.": ["DataFluss keeps data in databases of your own: on this PC, encrypted, and synced with a server if you wish, also after a time without a connection.", "DataFluss 将数据保存在你自己的数据库中：在这台电脑上，已加密，并可选择与服务器同步，断线一段时间后也可以。", "DataFluss 將資料保存在你自己的資料庫中：在這台電腦上，已加密，並可選擇與伺服器同步，離線一段時間後也可以。"],
    "Eine Datenbank beginnt mit einem Ausgangspunkt wie der Schulverwaltung und lässt sich dann mit eigenen Feldern, Masken und Druckvorlagen anpassen.": ["A database starts from a module such as school administration and can then be shaped with your own fields, forms and print templates.", "数据库从学校管理等起点开始，之后可用自定义字段、表单和打印模板调整。", "資料庫從學校管理等起點開始，之後可用自訂欄位、表單和列印範本調整。"],
    "Vier Wege zum Anfang": ["Four ways to begin", "四种开始方式", "四種開始方式"],
    "der schnellste Weg, alles kennenzulernen": ["the quickest way to get to know everything", "了解一切的最快方式", "了解一切的最快方式"],
    "für die eigenen Daten, mit einem Ausgangspunkt": ["for your own data, from a starting point", "用于自己的数据，从一个起点开始", "用於自己的資料，從一個起點開始"],
    "eine Kopie, die sich mit dem Server Ihrer Organisation abgleicht": ["a copy that syncs with your organisation's server", "与你所在机构的服务器同步的副本", "與你所在機構的伺服器同步的副本"],
    "von einem Server oder einem anderen Mac": ["from a server or another Mac", "来自服务器或另一台 Mac", "來自伺服器或另一台 Mac"],
    "von einem Server oder einem anderen PC": ["from a server or another PC", "来自服务器或另一台电脑", "來自伺服器或另一台電腦"],
    "Sicher von Anfang an": ["Secure from the start", "从一开始就安全", "從一開始就安全"],
    "Jede Datenbank ist verschlüsselt. Die Schlüssel liegen im Schlüsselbund dieses Macs, nie im Ordner der Datenbank.": ["Every database is encrypted. The keys are kept in this Mac's keychain, never in the database's folder.", "每个数据库都已加密。密钥保存在这台 Mac 的钥匙串中，从不放在数据库文件夹里。", "每個資料庫都已加密。金鑰保存在這台 Mac 的鑰匙圈中，從不放在資料庫資料夾裡。"],
    "Jede Datenbank ist verschlüsselt. Die Schlüssel liegen in der Anmeldeinformationsverwaltung dieses PCs, nie im Ordner der Datenbank.": ["Every database is encrypted. The keys are kept in this PC's Credential Manager, never in the database's folder.", "每个数据库都已加密。密钥保存在这台电脑的凭据管理器中，从不放在数据库文件夹里。", "每個資料庫都已加密。金鑰保存在這台電腦的認證管理員中，從不放在資料庫資料夾裡。"],
    "Beim ersten Anmelden zeigt ein kurzer Rundgang, wo was ist. Diese Seiten und den Rundgang gibt es jederzeit wieder im Menü Hilfe.": ["At the first sign-in, a short tour shows where everything is. These pages and the tour are always available again in the Help menu.", "首次登录时，简短的导览会介绍各项功能的位置。这些页面和导览随时可在“帮助”菜单中再次打开。", "首次登入時，簡短的導覽會介紹各項功能的位置。這些頁面和導覽隨時可在「說明」選單中再次開啟。"],
    "{i} von {n}": ["{i} of {n}", "第 {i} 页，共 {n} 页", "第 {i} 頁，共 {n} 頁"],
    "Selbst anfangen": ["Start on my own", "自己开始", "自己開始"],
    "Erst die Demo ansehen": ["See the demo first", "先看演示", "先看示範"],
    "Überspringen": ["Skip", "跳过", "略過"],
    "Der Server hat die Verbindung von „{name}“ zu diesem Mac getrennt. Die Kopie der Daten wurde von diesem Mac gelöscht. Zum weiteren Arbeiten den Mac neu verbinden oder die Webversion benutzen.": ["The server has disconnected “{name}” from this Mac. The copy of the data was deleted from this Mac. To keep working, connect the Mac again or use the web version.", "服务器已断开“{name}”与这台 Mac 的连接。数据副本已从这台 Mac 删除。如需继续工作，请重新连接这台 Mac 或使用网页版。", "伺服器已中斷「{name}」與這台 Mac 的連線。資料副本已從這台 Mac 刪除。如需繼續工作，請重新連線這台 Mac 或使用網頁版。"],
    "Der Server hat die Verbindung von „{name}“ zu diesem PC getrennt. Die Kopie der Daten wurde von diesem PC gelöscht. Zum weiteren Arbeiten den PC neu verbinden oder die Webversion benutzen.": ["The server has disconnected “{name}” from this PC. The copy of the data was deleted from this PC. To keep working, connect the PC again or use the web version.", "服务器已断开“{name}”与这台电脑的连接。数据副本已从这台电脑删除。如需继续工作，请重新连接这台电脑或使用网页版。", "伺服器已中斷「{name}」與這台電腦的連線。資料副本已從這台電腦刪除。如需繼續工作，請重新連線這台電腦或使用網頁版。"],
    "Verbindung getrennt": ["Disconnected", "连接已断开", "連線已中斷"],
    // progress the sidecar reports while it makes a database (client/sidecar/sidecar.py)
    "Datenbank wird eingerichtet": ["Setting up the database", "正在设置数据库", "正在設定資料庫"],
    "Beispieldaten werden angelegt": ["Creating sample data", "正在创建示例数据", "正在建立範例資料"],
    "Vorlage wird angewendet": ["Applying template", "正在应用模板", "正在套用範本"],
    "Zugangsdaten werden entfernt": ["Removing logins", "正在删除账号信息", "正在刪除帳號資訊"],
  };
  const COLUMN = { en: 0, "zh-hans": 1, "zh-hant": 2 };

  /** The text in the chosen language, with {name} replaced by vars.name. Unknown texts stay German. */
  function t(text, vars = {}) {
    const row = T[text];
    const out = language === "de" || !row ? text : row[COLUMN[language]];
    return out.replace(/\{(\w+)\}/g, (match, key) => (key in vars ? String(vars[key]) : match));
  }

  const ATTRIBUTES = ["placeholder", "aria-label", "title", "data-win", "data-win-label"];

  /** Translates the static texts of a page or a template's copy (text nodes and a few attributes). */
  function translate(root) {
    if (language === "de") return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const key = node.nodeValue.replace(/\s+/g, " ").trim();
      if (key && T[key]) node.nodeValue = node.nodeValue.replace(node.nodeValue.trim(), t(key));
    }
    const elements = root.querySelectorAll ? root.querySelectorAll("*") : [];
    for (const el of elements) {
      for (const name of ATTRIBUTES) {
        const value = el.getAttribute(name);
        if (value && T[value.replace(/\s+/g, " ").trim()]) el.setAttribute(name, t(value.replace(/\s+/g, " ").trim()));
      }
    }
  }

  function setLanguage(code) {
    try { localStorage.setItem(KEY, code); } catch {}
    location.reload();
  }

  const locale = { de: "de-DE", en: "en-GB", "zh-hans": "zh-CN", "zh-hant": "zh-TW" }[language];
  return { language, locale, languages: LANGUAGES, t, translate, setLanguage };
})();
