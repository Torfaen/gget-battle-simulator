# GGET 战斗模拟

这个项目包含《SD 高达 G 世纪 永恒》的本地战斗模拟。桌面窗口通过独立 ARM64 进程调用原版战斗服务，支持手动操作、战斗存档和 MuMu 续战。

## 启动手操窗口

准备 Windows、Python/Tk、原版 ARM64 运行环境和本地关卡数据。先配置外部数据目录，再从仓库根目录启动。

```powershell
$env:GGET_BATTLE_DATA_DIR = 'D:\GGET-battle-local'
python -B original_battle_reuse/battle_ui.py
```

已准备过运行环境的用户可以指向原工作区复用本地数据。窗口默认通过 ADB 启动 MuMu 内的独立战斗后端，国际版 2.8.0 的已结算我方检查点可用于续战。依赖、目录和准备命令见 [原版后端运行指南](original_battle_reuse/README.md)。

## 开发与回归

- 原版后端的桥接、战斗指令、存档和桌面 UI 位于 [`original_battle_reuse/`](original_battle_reuse/)。
- 启动入口、协议和实机验证记录见 [战斗模拟开发文档](docs/BATTLE_DEVELOPMENT.md)。

```powershell
python -B -m unittest discover -s original_battle_reuse -q
```

未配置原版数据时，依赖实机编队或原版资源的用例会跳过。运行时、主表、提取资源、检查点和账户编队保存在源码目录之外。
