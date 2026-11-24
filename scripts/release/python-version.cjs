const fs = require("node:fs/promises");
const path = require("node:path");

function verifyVersion(version) {
  if (
    typeof version !== "string" ||
    version.trim() !== version ||
    !/^16\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(version)
  ) {
    throw new Error(
      "version-16 can only publish stable 16.x.x releases. Review BREAKING CHANGE commits before releasing."
    );
  }
}

async function versionFile(cwd) {
  const filename = path.join(cwd, "vuna_transport", "__init__.py");
  const content = await fs.readFile(filename, "utf8");
  const pattern = /^__version__ = "\d+\.\d+\.\d+"$/gm;
  if ([...content.matchAll(pattern)].length !== 1) {
    throw new Error(
      "Expected exactly one stable __version__ assignment; refusing to rewrite the file."
    );
  }
  return { filename, content, pattern };
}

async function verifyRelease(_config, { cwd, nextRelease }) {
  verifyVersion(nextRelease.version);
  await versionFile(cwd);
}

async function prepare(_config, { cwd, nextRelease }) {
  verifyVersion(nextRelease.version);
  const { filename, content, pattern } = await versionFile(cwd);
  await fs.writeFile(
    filename,
    content.replace(pattern, `__version__ = "${nextRelease.version}"`)
  );
}

module.exports = { verifyRelease, prepare };
