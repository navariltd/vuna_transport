const path = require("node:path");

const repositoryRoot = path.resolve(__dirname, "../..");

async function runRelease(options = {}, context = {}) {
  // Load tools installed beside this runner, but keep Git, configuration,
  // Python version updates and release assets relative to the app root.
  const { default: semanticRelease } = await import("semantic-release");
  return semanticRelease(options, {
    ...context,
    cwd: context.cwd || repositoryRoot,
  });
}

if (require.main === module) {
  runRelease().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}

module.exports = { repositoryRoot, runRelease };
