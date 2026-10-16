# scad-ai-ide-builder

## 本地预览扩展开发

修改 `sources/vscode-openscad-preview` 后，在 Builder 根目录执行：

```sh
npm run build:extensions -- --preview=working-tree
npm run dev:vscode
```

`working-tree` 会复制子模块当前源码（包含未提交修改）到临时构建工作树，再打包并装配到 Dev Stage。默认配置仍从固定 Tag 构建；`submodule` 只读取子模块当前提交，不包含未提交改动。
