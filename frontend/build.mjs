import { execSync } from "node:child_process";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const out = "../static/vendor";
mkdirSync(out, { recursive: true });
const files = {
  "htmx.org/dist/htmx.min.js": "htmx.min.js",
  "alpinejs/dist/cdn.min.js": "alpine.min.js",
  "sortablejs/Sortable.min.js": "Sortable.min.js",
  "tabulator-tables/dist/js/tabulator.min.js": "tabulator.min.js",
  "tabulator-tables/dist/css/tabulator_simple.min.css": "tabulator.min.css",
  "pdfjs-dist/build/pdf.min.mjs": "pdf.min.mjs",
  "pdfjs-dist/build/pdf.worker.min.mjs": "pdf.worker.min.mjs",
};
// Map files are not shipped, and collectstatic stops at a reference to a missing file.
const withoutMap = (text) => text.replace(/\/[/*]# sourceMappingURL=[^\n*]*(\*\/)?\n?/g, "");
for (const [from, to] of Object.entries(files)) {
  if (/tabulator/.test(from)) writeFileSync(join(out, to), withoutMap(readFileSync(join("node_modules", from), "utf8")));
  else copyFileSync(join("node_modules", from), join(out, to));
}

const aliases = {
  home: "house", users: "users", briefcase: "briefcase", heart: "heart", school: "school", laptop: "laptop",
  wrench: "wrench", key: "key-round", card: "id-card", printer: "printer", swap: "arrow-left-right",
  settings: "settings", search: "search", plus: "plus", x: "x", check: "check", eye: "eye", "eye-off": "eye-off",
  copy: "copy", trash: "trash-2", download: "download", upload: "upload", sun: "sun", moon: "moon", logout: "log-out",
  menu: "menu", grip: "grip-vertical", chevron: "chevron-right", "chevron-down": "chevron-down", filter: "list-filter",
  columns: "columns-3", save: "save", undo: "undo-2", history: "history", alert: "triangle-alert", shield: "shield-check",
  file: "file-text", paperclip: "paperclip", message: "message-square", sparkles: "sparkles", refresh: "refresh-cw",
  layout: "layout-template", list: "list", user: "user", "user-plus": "user-plus", image: "image", qr: "qr-code",
  type: "type", heading: "heading", minus: "minus", table: "table-2", pen: "pen-line", code: "code-xml", tag: "tag",
  archive: "archive", "archive-restore": "archive-restore", monitor: "monitor", external: "external-link", info: "info",
  calendar: "calendar", book: "book-open", graduation: "graduation-cap", clipboard: "clipboard-check", copyplus: "copy-plus", move: "move", rows: "rows-3", wand: "wand-sparkles", lock: "lock", help: "circle-question-mark",
};
let sprite = '<svg xmlns="http://www.w3.org/2000/svg">';
for (const [name, file] of Object.entries(aliases)) {
  const path = `node_modules/lucide-static/icons/${file}.svg`;
  if (!existsSync(path)) { console.error("missing icon", file); continue; }
  const inner = readFileSync(path, "utf8").replace(/<!--[\s\S]*?-->/g, "").replace(/<svg[^>]*>/, "").replace("</svg>", "");
  sprite += `<symbol id="${name}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${inner}</symbol>`;
}
writeFileSync("../static/img/icons.svg", sprite + "</svg>");
execSync("npx @tailwindcss/cli -i app.css -o ../static/css/app.css --minify", { stdio: "inherit" });
