const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const versionPlugin = require("./python-version.cjs");
const { waitForCI } = require("./wait-for-ci.cjs");
const { repositoryRoot, runRelease } = require("./run.cjs");

const sha = "a".repeat(40);
const config = {
  repository: "navariltd/vuna_transport",
  sha,
  token: "test-token",
};
const success = {
  id: 1,
  head_sha: sha,
  head_branch: "version-16",
  event: "push",
  status: "completed",
  conclusion: "success",
};

function requestFor(runs, head = sha) {
  return async (url) => ({
    ok: true,
    json: async () =>
      url.includes("git/ref/")
        ? { object: { sha: head } }
        : { workflow_runs: runs },
  });
}

test("release gate requires successful tests and linters for the exact stable commit", async () => {
  const urls = [];
  const request = requestFor([success]);
  assert.equal(
    await waitForCI({
      ...config,
      request: (url, options) => {
        urls.push(url);
        assert.equal(options.headers.Authorization, "Bearer test-token");
        return request(url);
      },
    }),
    true
  );
  assert.equal(urls.length, 3);
  assert.ok(urls.some((url) => url.includes("ci.yml/runs")));
  assert.ok(urls.some((url) => url.includes("linter.yml/runs")));
});

test("failed, cancelled, or skipped checks block release", async () => {
  for (const conclusion of ["failure", "cancelled", "skipped", "timed_out"]) {
    await assert.rejects(
      waitForCI({
        ...config,
        request: requestFor([{ ...success, conclusion }]),
      }),
      /release blocked/
    );
  }
});

test("checks on another SHA, branch or event cannot authorize release", async () => {
  for (const change of [
    { head_sha: "b".repeat(40) },
    { head_branch: "develop" },
    { event: "pull_request" },
  ]) {
    await assert.rejects(
      waitForCI({
        ...config,
        request: requestFor([{ ...success, ...change }]),
        attempts: 1,
      }),
      /Timed out/
    );
  }
});

test("a newer failed run takes precedence over an older successful run", async () => {
  await assert.rejects(
    waitForCI({
      ...config,
      request: requestFor([
        success,
        { ...success, id: 2, conclusion: "failure" },
      ]),
    }),
    /release blocked/
  );
});

test("missing checks wait and then time out without releasing", async () => {
  let waits = 0;
  await assert.rejects(
    waitForCI({
      ...config,
      request: requestFor([]),
      attempts: 2,
      sleep: async () => waits++,
    }),
    /Timed out/
  );
  assert.equal(waits, 1);
});

test("one successful workflow cannot authorize release while the other is missing", async () => {
  const request = requestFor([success]);
  await assert.rejects(
    waitForCI({
      ...config,
      attempts: 1,
      request: (url, options) =>
        url.includes("linter.yml")
          ? requestFor([])(url, options)
          : request(url, options),
    }),
    /Timed out/
  );
});

test("a superseded stable commit is skipped", async () => {
  assert.equal(
    await waitForCI({ ...config, request: requestFor([], "b".repeat(40)) }),
    false
  );
});

test("API errors and absent credentials fail closed without exposing credentials", async () => {
  await assert.rejects(
    waitForCI({ ...config, token: "" }),
    /Missing or invalid/
  );
  await assert.rejects(
    waitForCI({ ...config, request: async () => ({ ok: false, status: 403 }) }),
    /failed \(403\)/
  );
});

test("only the Python version assignment changes and stable 16.x releases are allowed", async (t) => {
  const cwd = await fs.mkdtemp(
    path.join(os.tmpdir(), "transport-release-test-")
  );
  t.after(() => fs.rm(cwd, { recursive: true, force: true }));
  await fs.mkdir(path.join(cwd, "vuna_transport"));
  const filename = path.join(cwd, "vuna_transport", "__init__.py");
  const original =
    '__version__ = "16.0.0"\n# Preserve the rest of this module.\n';
  await fs.writeFile(filename, original);
  const context = { cwd, nextRelease: { version: "16.1.2" } };
  await versionPlugin.verifyRelease({}, context);
  await versionPlugin.prepare({}, context);
  assert.equal(
    await fs.readFile(filename, "utf8"),
    original.replace('"16.0.0"', '"16.1.2"')
  );
  for (const version of [
    "17.0.0",
    "1.0.1",
    "16.1.0-beta.1",
    "16.01.0",
    "16.1.0\nmalicious",
  ]) {
    await assert.rejects(
      versionPlugin.verifyRelease({}, { cwd, nextRelease: { version } }),
      /only publish stable/
    );
    await assert.rejects(
      versionPlugin.prepare({}, { cwd, nextRelease: { version } }),
      /only publish stable/
    );
  }
  assert.equal(
    await fs.readFile(filename, "utf8"),
    original.replace('"16.0.0"', '"16.1.2"')
  );
  await fs.writeFile(
    filename,
    '__version__ = "16.0.0"\n__version__ = "16.0.1"\n'
  );
  await assert.rejects(versionPlugin.prepare({}, context), /exactly one/);
});

test("semantic-release recognizes fixes, features, and breaking changes without publishing", async () => {
  const { analyzeCommits } = await import("@semantic-release/commit-analyzer");
  for (const [message, expected] of [
    ["fix(planning): preserve requested UOM", "patch"],
    ["feat(fleet): support additional vehicles", "minor"],
    ["chore(ci): configure release tooling", null],
    [
      "feat(api): replace payload\n\nBREAKING CHANGE: remove the old contract",
      "major",
    ],
  ]) {
    assert.equal(
      await analyzeCommits(
        { preset: "angular" },
        {
          cwd: process.cwd(),
          commits: [{ hash: sha, message }],
          logger: { log() {} },
        }
      ),
      expected
    );
  }
});

test("semantic-release starts at v16.0.0 and previews the next patch without publishing", async (t) => {
  const fixture = await fs.mkdtemp(
    path.join(os.tmpdir(), "transport-semantic-release-test-")
  );
  t.after(() => fs.rm(fixture, { recursive: true, force: true }));
  const cwd = path.join(fixture, "repo");
  const remote = path.join(fixture, "remote.git");
  await fs.mkdir(cwd);
  const git = (...args) =>
    execFileSync("git", ["-c", "core.hooksPath=/dev/null", ...args], {
      cwd,
      encoding: "utf8",
      stdio: "pipe",
    });
  git("init", "--bare", "--initial-branch=version-16", remote);
  git("init", "--initial-branch=version-16");
  git("config", "user.name", "Release Test");
  git("config", "user.email", "release-test@example.invalid");
  await fs.mkdir(path.join(cwd, "vuna_transport"));
  const original = '__version__ = "16.0.0"\n';
  await fs.writeFile(path.join(cwd, "vuna_transport", "__init__.py"), original);
  git("add", "vuna_transport/__init__.py");
  git("commit", "-m", "chore: initial release");
  git("tag", "v16.0.0");
  git(
    "commit",
    "--allow-empty",
    "-m",
    "fix(planning): correct a quantity check"
  );
  git("remote", "add", "origin", remote);
  git("push", "origin", "version-16", "--tags");
  const before = git("rev-parse", "HEAD");
  const result = await runRelease(
    {
      branches: ["version-16"],
      repositoryUrl: remote,
      tagFormat: "v${version}",
      dryRun: true,
      ci: false,
      plugins: [
        [
          require.resolve("@semantic-release/commit-analyzer"),
          { preset: "angular" },
        ],
        path.join(__dirname, "python-version.cjs"),
      ],
    },
    // Do not let the real Actions PR/branch identity leak into this local fixture.
    {
      cwd,
      env: { PATH: process.env.PATH, CI: "false", GIT_CONFIG_NOSYSTEM: "1" },
    }
  );
  assert.equal(result.lastRelease.version, "16.0.0");
  assert.equal(result.nextRelease.version, "16.0.1");
  assert.equal(git("rev-parse", "HEAD"), before);
  assert.equal(git("tag", "--list").trim(), "v16.0.0");
  assert.equal(
    await fs.readFile(path.join(cwd, "vuna_transport", "__init__.py"), "utf8"),
    original
  );
});

test("Bench installation cannot discover a root release-tool package", async () => {
  assert.equal(repositoryRoot, path.resolve(__dirname, "../.."));
  for (const filename of ["package.json", "package-lock.json"]) {
    await assert.rejects(fs.access(path.join(repositoryRoot, filename)), {
      code: "ENOENT",
    });
  }
  const manifest = JSON.parse(
    await fs.readFile(path.join(__dirname, "package.json"), "utf8")
  );
  assert.equal(manifest.private, true);
  assert.equal(manifest.scripts.release, "node run.cjs");
});

test("release plugins resolve from isolated tools while local plugins stay at the repository root", async () => {
  const entry = require.resolve("semantic-release");
  assert.ok(entry.startsWith(path.join(__dirname, "node_modules") + path.sep));
  const { pathToFileURL } = require("node:url");
  const { loadPlugin } = await import(
    pathToFileURL(path.join(path.dirname(entry), "lib/plugins/utils.js")).href
  );
  const config = JSON.parse(
    await fs.readFile(path.join(repositoryRoot, ".releaserc"), "utf8")
  );
  assert.deepEqual(config.branches, ["version-16"]);
  assert.equal(config.tagFormat, "v${version}");
  for (const plugin of config.plugins) {
    const name = Array.isArray(plugin) ? plugin[0] : plugin;
    const loaded = await loadPlugin({ cwd: repositoryRoot }, name, {});
    assert.ok(
      loaded &&
        Object.values(loaded).some((value) => typeof value === "function"),
      name
    );
  }
});

test("CI and publishing install release tools without a root manifest or lockfile cache", async () => {
  for (const filename of ["ci.yml", "release.yml"]) {
    const workflow = await fs.readFile(
      path.join(repositoryRoot, ".github/workflows", filename),
      "utf8"
    );
    assert.match(
      workflow,
      /npm install --prefix scripts\/release --package-lock=false --ignore-scripts/
    );
    assert.doesNotMatch(workflow, /npm ci|cache: npm/);
  }
  const publishing = await fs.readFile(
    path.join(repositoryRoot, ".github/workflows/release.yml"),
    "utf8"
  );
  assert.match(publishing, /npm --prefix scripts\/release run release/);
  assert.match(publishing, /git rev-parse HEAD/);
  const ignore = await fs.readFile(
    path.join(repositoryRoot, ".gitignore"),
    "utf8"
  );
  for (const filename of [
    "/package-lock.json",
    "/yarn.lock",
    "/scripts/release/package-lock.json",
    "/scripts/release/yarn.lock",
  ]) {
    assert.ok(ignore.split(/\r?\n/).includes(filename), filename);
  }
});
