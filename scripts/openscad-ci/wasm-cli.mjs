#!/usr/bin/env node
// Map host paths into NodeFS so one wasm32 module works on POSIX and Windows.
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import OpenSCAD from './openscad-core.mjs';

if (Number(process.versions.node.split('.')[0]) < 22) {
  console.error('OpenSCAD WASM requires Node.js 22 or newer.');
  process.exit(1);
}
const mounts = new Map();
function virtualPath(value) {
  const absolute = path.resolve(value);
  const root = path.parse(absolute).root;
  const mount = process.platform === 'win32'
    ? '/host/' + root.replaceAll('\\', '/').replace(/[:/]+/g, '_')
    : '/host';
  mounts.set(mount, root);
  return mount + '/' + absolute.slice(root.length).replaceAll('\\', '/');
}

const filename = fileURLToPath(import.meta.url);
const resources = path.resolve(path.dirname(filename), '../share/openscad');
const program = virtualPath(filename);
const cwd = virtualPath(process.cwd());
const fonts = virtualPath(path.join(resources, 'fonts'));
const libraries = [path.join(resources, 'libraries'),
  ...(process.env.OPENSCADPATH || '').split(path.delimiter).filter(Boolean)].map(virtualPath);
const extraFonts = (process.env.OPENSCAD_FONT_PATH || '').split(path.delimiter).filter(Boolean).map(virtualPath);
const args = process.argv.slice(2);
const pathOptions = new Set(['-o', '--o', '-d', '--d', '-I', '--include']);
let expectsPath = false;
for (let i = 0; i < args.length; i++) {
  const arg = args[i];
  if (expectsPath) {
    args[i] = arg === '-' ? arg : virtualPath(arg);
    expectsPath = false;
  } else if (pathOptions.has(arg)) {
    expectsPath = true;
  } else if (/^(--o=|--d=|--include=|-I.+)/.test(arg)) {
    const prefix = arg.startsWith('-I') ? '-I' : arg.slice(0, arg.indexOf('=') + 1);
    args[i] = prefix + virtualPath(arg.slice(prefix.length));
  } else if (path.isAbsolute(arg)) {
    args[i] = virtualPath(arg);
  }
}
const instance = await OpenSCAD({
  noInitialRun: true,
  thisProgram: program,
  preRun(module) {
    for (const [mount, root] of mounts) {
      module.FS.mkdirTree(mount);
      module.FS.mount(module.NODEFS, { root }, mount);
    }
    module.FS.chdir(cwd);
    module.ENV.OPENSCADPATH = libraries.join(':');
    module.ENV.OPENSCAD_FONT_PATH = [fonts, ...extraFonts].join(':');
    module.ENV.FONTCONFIG_PATH = fonts;
    module.ENV.FONTCONFIG_FILE = fonts + '/fonts.conf';
    module.ENV.LANG = 'C.UTF-8';
  },
});
instance.callMain(args);
