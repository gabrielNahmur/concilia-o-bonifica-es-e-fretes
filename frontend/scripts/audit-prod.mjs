import { spawnSync } from "node:child_process";

// This SPA does not use React Server Components or React Router Actions. Keep
// this exception narrow: any other high/critical advisory still fails CI.
const allowedAdvisories = new Set([
  "https://github.com/advisories/GHSA-qwww-vcr4-c8h2",
]);

const result = spawnSync("npm", ["audit", "--omit=dev", "--json"], {
  encoding: "utf8",
  shell: process.platform === "win32",
});
let report;
try {
  report = JSON.parse(result.stdout);
} catch {
  process.stderr.write(result.stderr || result.stdout || "npm audit did not return JSON\n");
  process.exit(1);
}

const blocking = [];
for (const vulnerability of Object.values(report.vulnerabilities || {})) {
  for (const advisory of vulnerability.via || []) {
    if (typeof advisory !== "object") continue;
    if (!["high", "critical"].includes(advisory.severity)) continue;
    if (!allowedAdvisories.has(advisory.url)) blocking.push(advisory);
  }
}

if (blocking.length) {
  for (const advisory of blocking) {
    console.error(`${advisory.severity}: ${advisory.title} (${advisory.url})`);
  }
  process.exit(1);
}
console.log("Production dependency audit passed; the RSC-only React Router advisory is not applicable to this SPA.");
