#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import { existsSync, lstatSync, realpathSync, rmSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const args = process.argv.slice(2);
if (args.includes('--help') || args.includes('-h')) {
  console.log('Usage: npm run reset:vscode [-- --all]');
  console.log('Remove saved development settings, state and caches.');
  console.log('  --all  Also remove user-installed development extensions.');
  process.exit(0);
}
if (args.some(arg => arg !== '--all')) {
  console.error('ERROR: Unknown option. Use --help for usage.');
  process.exit(1);
}

const root = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const dev = path.join(root, '.dev');
const targets = [path.join(dev, 'user-data')];
if (args.includes('--all')) targets.push(path.join(dev, 'extensions'));

try {
  // Never follow a redirected development directory when resetting local data.
  for (const directory of [dev, ...targets]) {
    if (existsSync(directory) && lstatSync(directory).isSymbolicLink()) {
      throw new Error(`Refusing to reset a symbolic link: ${directory}`);
    }
  }

  // Check before deleting anything. Fail closed if the process list is unavailable.
  const processes = execFileSync('ps', ['-ax', '-o', 'command='], { encoding: 'utf8' });
  const invokedDev = path.resolve(path.dirname(process.argv[1]), '..', '.dev');
  const paths = [dev, invokedDev, existsSync(dev) ? realpathSync(dev) : dev];
  const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const running = paths.some(directory => new RegExp(
    `--(?:user-data-dir|extensions-dir)(?:=|\\s+)["']?${escape(directory)}[\\/]` +
    `(?:user-data|extensions)["']?(?:\\s|$)`,
  ).test(processes));
  if (running) {
    throw new Error('Development VSCode is still running. Quit that instance before resetting.');
  }

  for (const target of targets) {
    rmSync(target, { recursive: true, force: true });
    console.log(`==> Removed ${path.relative(root, target)}`);
  }
  console.log('Development profile reset. The next dev:vscode launch creates a new profile.');
} catch (error) {
  console.error(`ERROR: ${error.message}`);
  process.exit(1);
}
