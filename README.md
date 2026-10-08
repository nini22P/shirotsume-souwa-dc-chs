# 白诘草话 Dreamcast 版本 简体中文汉化项目

文本移植自 [elementary-particle / my-shirotsume-souwa-patch](https://github.com/elementary-particle/my-shirotsume-souwa-patch)，缺乏校对，某些对话排版错乱，未汉化图片。

## 如何打包

打包需要 Windows 下的 bash 环境，如果安装了 git 无需重新下载安装，可直接使用。

1. 新建 `raw/gdi` 文件夹，放入 gdi 镜像。
2. 在项目路径打开控制台，运行 `sh pack.sh`。

流程结束后汉化镜像和对应的 xdelta 补丁将保存在 `build/gdi`。

## 注意事项

- 字库有 1808 的上限，修改 `scr.csv` 和 `bin.csv` 时尽量不要使用生僻字。
- 脚本中的 `,` 和 `.` 用途是停顿控制节奏，当前译文中大量使用了 `･` 未修复。
- 图像工具未完成。

## 鸣谢

- [elementary-particle / my-shirotsume-souwa-patch](https://github.com/elementary-particle/my-shirotsume-souwa-patch)
- [amayra / arc_conv](https://github.com/amayra/arc_conv)
