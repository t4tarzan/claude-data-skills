#!/usr/bin/env node
'use strict';
/*
 * npx data-team-skill  —  install the `data-team` plugin into Claude Code.
 * A thin, cross-platform launcher around the native `claude plugin` commands.
 * Re-run any time (npx data-team-skill@latest) to pull the newest version.
 */
const { spawnSync } = require('child_process');

const REPO = 't4tarzan/claude-data-skills'; // marketplace source (GitHub)
const MARKET = 'claude-data-skills';        // marketplace name (marketplace.json `name`)
const PLUGIN = 'data-team';                 // plugin name

const P = (c) => `\x1b[1;35m${c}\x1b[0m`;   // purple
const G = (c) => `\x1b[32m${c}\x1b[0m`;
const R = (c) => `\x1b[31m${c}\x1b[0m`;

function hasClaude() {
  try { return spawnSync('claude', ['--version'], { stdio: 'ignore' }).status === 0; }
  catch (_) { return false; }
}
function claude(args) { return spawnSync('claude', args, { stdio: 'inherit' }); }

console.log('\n' + P('data-team') + ' — a whole data team as one Claude Code command\n');

if (!hasClaude()) {
  console.log("The 'claude' CLI was not found on your PATH.");
  console.log('Install Claude Code (https://claude.com/claude-code), then run these inside a session:\n');
  console.log(`  /plugin marketplace add ${REPO}`);
  console.log(`  /plugin install ${PLUGIN}@${MARKET}\n`);
  console.log('…and invoke it with:  /data-team:run');
  process.exit(0);
}

console.log(`→ adding marketplace ${REPO}`);
if (claude(['plugin', 'marketplace', 'add', REPO]).status !== 0) {
  // already added -> refresh to latest
  claude(['plugin', 'marketplace', 'update', MARKET]);
}
console.log(`→ installing ${PLUGIN}@${MARKET}`);
const res = claude(['plugin', 'install', `${PLUGIN}@${MARKET}`]);

if (res.status === 0) {
  console.log('\n' + G('✓ installed') + ' — invoke it in Claude Code:  /data-team:run');
  console.log(`  keep current:  /plugin  →  Marketplaces  →  ${MARKET}  →  Enable auto-update`);
} else {
  console.log('\n' + R('✗ install failed') + ` — try inside a session:  /plugin install ${PLUGIN}@${MARKET}`);
  process.exit(1);
}
