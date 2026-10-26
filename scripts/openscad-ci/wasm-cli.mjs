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
// The historical NULLGL renderer can report success after writing a
// placeholder framebuffer. Fail explicitly for formats absent from this build.
const unsupported = new Set(['png', 'pdf', '3mf']);
const outputs = [];
let exportFormat;
for (let i = 0; i < args.length; i++) {
  const arg = args[i];
  if (arg === '--export-format') exportFormat = args[++i];
  else if (arg.startsWith('--export-format=')) exportFormat = arg.slice('--export-format='.length);
  else if (arg === '-o' || arg === '--o') outputs.push(args[++i]);
  else if (arg.startsWith('--o=')) outputs.push(arg.slice(4));
  else if (arg.startsWith('-o')) outputs.push(arg.slice(2));
}
for (const output of outputs) {
  const format = (exportFormat || path.extname(output || '').slice(1)).toLowerCase();
  if (unsupported.has(format)) {
    console.error(`OpenSCAD WASM does not support ${format.toUpperCase()} output; select a native runtime for this format.`);
    process.exit(2);
  }
}
const pathOptions = new Set(['-o', '--o', '-d', '--d', '-I', '--include']);
let expectsPath = false;
for (let i = 0; i < args.length; i++) {
  const arg = args[i];
  if (expectsPath) {
    args[i] = arg === '-' ? arg : virtualPath(arg);
    expectsPath = false;
  } else if (pathOptions.has(arg)) {
    expectsPath = true;
  } else if (/^(--o=|--d=|--include=|-[odI].+)/.test(arg)) {
    const prefix = arg.startsWith('--') ? arg.slice(0, arg.indexOf('=') + 1) : arg.slice(0, 2);
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
