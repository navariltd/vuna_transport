const fs = require("node:fs");

const WORKFLOWS = ["ci.yml", "linter.yml"];

async function waitForCI({
  repository,
  sha,
  token,
  request = fetch,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  attempts = 120,
}) {
  if (
    !/^[\w.-]+\/[\w.-]+$/.test(repository || "") ||
    !/^[a-f0-9]{40}$/.test(sha || "") ||
    !token
  ) {
    throw new Error(
      "Missing or invalid release repository, commit or GitHub token."
    );
  }
  async function get(resource) {
    const response = await request(
      `https://api.github.com/repos/${repository}/${resource}`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
        },
      }
    );
    if (!response.ok)
      throw new Error(
        `GitHub release readiness request failed (${response.status}).`
      );
    return response.json();
  }
  for (let attempt = 0; attempt < attempts; attempt++) {
    const ref = await get("git/ref/heads/version-16");
    if (ref.object.sha !== sha) return false;
    let ready = true;
    for (const workflow of WORKFLOWS) {
      const result = await get(
        `actions/workflows/${workflow}/runs?branch=version-16&event=push&head_sha=${sha}&per_page=100`
      );
      const run = result.workflow_runs
        .filter(
          (row) =>
            row.head_sha === sha &&
            row.head_branch === "version-16" &&
            row.event === "push"
        )
        .sort((a, b) => b.id - a.id)[0];
      if (!run || run.status !== "completed") {
        ready = false;
      } else if (run.conclusion !== "success") {
        throw new Error(`${workflow} did not succeed; release blocked.`);
      }
    }
    if (ready) return true;
    if (attempt + 1 < attempts) await sleep(15000);
  }
  throw new Error(
    "Timed out waiting for successful tests and linters; release blocked."
  );
}

if (require.main === module) {
  waitForCI({
    repository: process.env.GITHUB_REPOSITORY,
    sha: process.env.GITHUB_SHA,
    token: process.env.GITHUB_TOKEN,
  })
    .then((ready) => {
      if (!process.env.GITHUB_OUTPUT)
        throw new Error("Missing GitHub Actions output path.");
      fs.appendFileSync(process.env.GITHUB_OUTPUT, `ready=${ready}\n`);
      console.log(
        ready
          ? "Tests and linters passed for the stable commit."
          : "Stable branch advanced; skipping this commit."
      );
    })
    .catch((error) => {
      console.error(error.message);
      process.exitCode = 1;
    });
}

module.exports = { waitForCI };
