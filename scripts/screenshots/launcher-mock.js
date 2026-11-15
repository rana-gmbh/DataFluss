// A stand-in for the desktop shell, so the start screen (client/shell/ui) renders in a browser for
// screenshots (scripts/screenshots.py). Answers the shell commands launcher.js calls with invented data.
window.__TAURI__ = {
  core: {
    invoke: async (command) => {
      switch (command) {
        case "list_databases": return [
          { id: "a1", name: "Schule Musterstadt", kind: "connected", school: "Schule Musterstadt", server: "datafluss.musterstadt.example", created: 1790000000, last_opened: 1794740000, size: 48200000, open: false },
          { id: "b2", name: "Demo", kind: "demo", school: "Schule Musterstadt", created: 1792000000, last_opened: 1794650000, size: 21400000, open: false },
          { id: "c3", name: "Grundschule Musterdorf", kind: "local", school: "Grundschule Musterdorf", created: 1793000000, last_opened: 1794300000, size: 3100000, open: false },
        ];
        case "available_modules": return [];
        case "welcome_seen": return true;
        default: return null;
      }
    },
  },
  event: { listen: async () => () => {} },
};
