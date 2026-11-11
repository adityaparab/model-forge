// Scripted recording of the console: drives one fake-profile run from submit to sealed,
// saves a screenshot per step under <out>/shots/, and turns the browser's WebM into
// <out>/demo.mp4 and <out>/demo.gif with ffmpeg.
//
//   node scripts/demo_record.mjs [--base URL] [--out DIR] [--brief PATH] [--no-video]
//
// Every step waits on page text or element state (never a timer), apart from a short settle
// before each screenshot. Exit codes: 0 ok, 1 a step or ffmpeg failed, 2 media over the limits.

import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
import { join } from "node:path";
import { parseArgs } from "node:util";

import { chromium } from "playwright";

const { values: args } = parseArgs({
  options: {
    base: { type: "string", default: "http://localhost:8000" },
    out: { type: "string", default: "docs/media" },
    brief: { type: "string", default: "examples/briefs/polish-pd.yaml" },
    "no-video": { type: "boolean", default: false },
  },
});

const BASE = args.base.replace(/\/$/, "");
const OUT = args.out;
const SHOTS = join(OUT, "shots");
const VIDEO = !args["no-video"];
const TIMEOUT = 120_000;
const SETTLE_MS = 600;
const VIEWPORT = { width: 1440, height: 900 };
const MIB = 1024 * 1024;
const LIMITS = { gif: 8 * MIB, mp4: 25 * MIB };

mkdirSync(SHOTS, { recursive: true });
const videoDir = VIDEO ? mkdtempSync(join(OUT, ".video-")) : null;
const briefText = readFileSync(args.brief, "utf8");

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: VIEWPORT,
  deviceScaleFactor: 1,
  colorScheme: "dark",
  ...(VIDEO ? { recordVideo: { dir: videoDir, size: VIEWPORT } } : {}),
});
context.setDefaultTimeout(TIMEOUT);
const page = await context.newPage();

async function step(name, body) {
  const started = Date.now();
  await body();
  await page.waitForTimeout(SETTLE_MS);
  await page.screenshot({ path: join(SHOTS, `${name}.png`) });
  console.log(`${name}  ${((Date.now() - started) / 1000).toFixed(1)} s`);
}

const header = () => page.getByRole("region", { name: "Run" });
const status = (text) => header().getByText(text, { exact: true });
const tab = (label) => page.getByRole("navigation", { name: "Run tabs" }).getByRole("link", { name: label, exact: true });
const submit = (decision) => page.getByRole("button", { name: `Record decision: ${decision}` });

async function identity(name) {
  const field = page.getByPlaceholder("your name");
  await field.fill(name);
  await field.press("Tab");
}

let runUrl = "";

async function overview() {
  await tab("Overview").click();
  await page.waitForURL(runUrl);
}

let failed = false;
try {
  await step("01-runs", async () => {
    await page.goto(`${BASE}/`);
    await identity("alice");
    await page.getByRole("heading", { level: 1 }).first().waitFor();
  });

  await step("02-new-run", async () => {
    await page.goto(`${BASE}/runs/new`);
    const profile = page.getByLabel("Profile");
    await profile.locator("option").first().waitFor({ state: "attached" });
    await page.getByLabel("Brief (YAML)").fill(briefText);
    await page.getByRole("button", { name: "Submit run" }).waitFor();
  });
  await page.getByRole("button", { name: "Submit run" }).click();
  await page.waitForURL(/\/runs\/run_[^/]+$/);
  runUrl = page.url();

  await step("03-scope-gate", async () => {
    await status("awaiting scope").waitFor();
    await page.getByRole("heading", { name: "Scope gate" }).waitFor();
  });

  await step("04-scope-approved", async () => {
    await submit("approve").click();
    await status("running").waitFor();
    await page.getByRole("region", { name: "Live events" }).waitFor();
  });

  await step("05-model-gate", async () => {
    await status("awaiting model").waitFor();
    await page.getByRole("heading", { name: "Model gate" }).waitFor();
  });

  await step("06-four-eyes", async () => {
    await submit("accept").click();
    await page.getByText("Decision refused").waitFor();
  });

  await step("07-model-accepted", async () => {
    await identity("bob");
    await submit("accept").click();
    // the gate panel unmounts as soon as the run moves on, so wait on the header's status
    await header().getByText(/^(running|awaiting signoff)$/).waitFor();
  });

  // the document and the challenger's findings are complete once the run parks at sign-off
  await status("awaiting signoff").waitFor();

  await step("08-document", async () => {
    await tab("Document").click();
    const chip = page.getByRole("button", { name: /^Citation \[A:/ }).first();
    await chip.scrollIntoViewIfNeeded();
    await chip.hover();
    await page.getByRole("dialog", { name: /^Citation \[A:/ }).first().waitFor();
  });

  await step("09-findings", async () => {
    await tab("Findings").click();
    await page.getByRole("region", { name: "Round 1" }).waitFor();
  });

  // sign-off before the lineage step: Verify needs the sealed manifest
  await step("12-signoff", async () => {
    await overview();
    await page.getByRole("heading", { name: "Sign-off gate" }).waitFor();
    await submit("approve").waitFor();
  });
  await submit("approve").click();
  await status("completed").waitFor();

  await step("10-lineage", async () => {
    await tab("Lineage").click();
    const verify = page.getByRole("button", { name: "Verify chain" });
    await verify.waitFor();
    await page.waitForFunction(
      () => [...document.querySelectorAll("button")].some((b) => b.textContent === "Verify chain" && !b.disabled),
      undefined,
      { timeout: TIMEOUT },
    );
    await verify.click();
    await page.getByLabel("Verify result").filter({ hasText: /^ok$/ }).waitFor();
  });

  await step("11-costs", async () => {
    await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Costs" }).click();
    await page.waitForURL(`${BASE}/costs`);
    await page.getByRole("heading", { level: 1 }).first().waitFor();
  });

  await step("13-sealed", async () => {
    await page.goto(runUrl);
    await status("completed").waitFor();
    const reproduced = page.getByText(/^Reproduced: \d+ artifacts matched$/);
    const deadline = Date.now() + TIMEOUT;
    // the replay's report can land after the event that refreshed the panel, so a page
    // that has not picked it up within a few seconds is reloaded
    for (;;) {
      try {
        await reproduced.waitFor({ timeout: 5_000 });
        break;
      } catch (error) {
        if (Date.now() > deadline) {
          const badge = await page.getByLabel("Reproducibility").textContent().catch(() => null);
          throw new Error(`no "Reproduced" badge (badge: ${badge ?? "none"})`, { cause: error });
        }
        await page.reload();
        await status("completed").waitFor();
      }
    }
    await page.getByRole("region", { name: "Replays" }).scrollIntoViewIfNeeded();
  });
} catch (error) {
  failed = true;
  console.error(`recording failed: ${error instanceof Error ? error.message : String(error)}`);
  await page.screenshot({ path: join(OUT, "failure.png") }).catch(() => {});
}

const video = page.video();
await context.close();
await browser.close();

if (failed) {
  if (videoDir !== null) rmSync(videoDir, { recursive: true, force: true });
  process.exit(1);
}

if (VIDEO && video !== null) {
  const webm = await video.path();
  const mp4 = join(OUT, "demo.mp4");
  const gif = join(OUT, "demo.gif");
  const palette = join(videoDir, "palette.png");
  const ffmpeg = (...ffArgs) => {
    const result = spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", ...ffArgs], { stdio: "inherit" });
    if (result.status !== 0) {
      rmSync(videoDir, { recursive: true, force: true });
      console.error(`ffmpeg failed: ${ffArgs.join(" ")}`);
      process.exit(1);
    }
  };
  ffmpeg("-y", "-i", webm, "-vf", "scale=1440:-2,format=yuv420p", "-c:v", "libx264", "-crf", "23", "-movflags", "+faststart", mp4);
  ffmpeg("-y", "-i", webm, "-vf", "fps=8,scale=720:-1:flags=lanczos,palettegen", palette);
  ffmpeg("-y", "-i", webm, "-i", palette, "-lavfi", "fps=8,scale=720:-1:flags=lanczos [x]; [x][1:v] paletteuse", gif);
  rmSync(videoDir, { recursive: true, force: true });

  const sizes = { gif: statSync(gif).size, mp4: statSync(mp4).size };
  const mb = (bytes) => `${(bytes / MIB).toFixed(2)} MB`;
  console.log(`demo.gif ${mb(sizes.gif)} (limit ${mb(LIMITS.gif)})`);
  console.log(`demo.mp4 ${mb(sizes.mp4)} (limit ${mb(LIMITS.mp4)})`);
  if (sizes.gif > LIMITS.gif || sizes.mp4 > LIMITS.mp4) {
    console.error("media over the size limits");
    process.exit(2);
  }
}
