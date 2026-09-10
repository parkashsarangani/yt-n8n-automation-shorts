/**
 * Test environment probes.
 * -------------------------------------------------------------------
 * Two kinds of test in this suite only make sense under specific conditions,
 * and previously reported a plain failure when those conditions were absent -
 * which is indistinguishable from a real regression:
 *
 *  1. Render tests boot the compositor and produce real MP4s. They need system
 *     FFmpeg plus Remotion's platform-native rspack binding, which CI installs
 *     and a typical dev machine (notably Windows) does not have.
 *  2. Production-invariant tests assert markers that only exist AFTER the build
 *     transforms run. CI copies the built artifacts over the source before
 *     running the suite ("Run regression tests on exact deploy artifacts"), so
 *     against raw source those assertions cannot hold.
 *
 * Both now skip with an explicit reason instead of failing.
 */
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

function hasSystemFfmpeg() {
  for (const bin of ["ffmpeg", "ffprobe"]) {
    try {
      execFileSync(bin, ["-version"], { stdio: "ignore" });
    } catch {
      return false;
    }
  }
  return true;
}

function hasRemotionBinding() {
  try {
    require.resolve("@rspack/binding", { paths: [path.join(__dirname, "..", "..", "remotion")] });
    return true;
  } catch {
    return false;
  }
}

/**
 * @returns {string|false} a skip reason, or false when the toolchain is present.
 */
function renderToolchainSkipReason() {
  if (!hasSystemFfmpeg()) return "requires system FFmpeg/ffprobe on PATH (installed in CI)";
  if (!hasRemotionBinding()) return "requires Remotion's platform-native rspack binding (installed in CI)";
  return false;
}

/**
 * Production invariants are only present in built artifacts. Returns a skip
 * reason when handed un-transformed source.
 * @returns {string|false}
 */
function builtArtifactSkipReason(relativePath, buildMarker) {
  const file = path.join(__dirname, "..", "..", relativePath);
  let source;
  try {
    source = fs.readFileSync(file, "utf8");
  } catch {
    return `${relativePath} is not present`;
  }
  if (!source.includes(buildMarker)) {
    return `${relativePath} is un-transformed source; this invariant only exists in the built artifact `
      + `(CI copies build output over source before running the suite)`;
  }
  return false;
}

function readArtifact(relativePath) {
  return fs.readFileSync(path.join(__dirname, "..", "..", relativePath), "utf8");
}

module.exports = {
  hasSystemFfmpeg,
  hasRemotionBinding,
  renderToolchainSkipReason,
  builtArtifactSkipReason,
  readArtifact,
};
