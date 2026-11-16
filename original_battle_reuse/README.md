# 原版后端运行指南

桌面窗口和命令行运行器在独立 ARM64 进程内加载原版 IL2CPP。普通窗口战斗和命令行回归使用固定的 2.7.0 运行文件。MuMu 检查点续战也需要已准备好的 2.7.0 基础运行目录、关卡和部署输入，负责恢复战斗的客体进程使用从 MuMu 安装包提取并校验过的 2.8.0 运行文件。

## 软件和依赖

- Windows 与 Python 3.10+，包含 Tk 桌面组件。
- MuMu 用于模拟器内启动和续战。Windows 独立后端使用 QEMU，准备工具需可找到 7-Zip。
- Python 原版后端依赖在 `requirements-native.txt`。需要从本地备份重新生成原生方法索引时，再安装 `requirements-battle-analysis.txt`。

```powershell
python -m pip install -r requirements-native.txt
python -m pip install --target "$env:GGET_BATTLE_DATA_DIR/original_battle_reuse/artifacts/zig_env" ziglang==0.15.2
```

执行前先设置 `GGET_BATTLE_DATA_DIR`。已有外部数据目录带 `zig_env` 时可复用其编译器。

## MuMu ADB 连接

启动 MuMu 并启用 ADB。程序先查找 `PATH` 中的工具以及它们所在的目录，再读取 Windows 安装记录，从 MuMu 的实际安装目录查找自带的 ADB 和 `MuMuManager.exe`。已有连接直接复用，需要重连时从管理器读取正在运行的实例及其 ADB 端口。

未登记到 Windows 的安装可在启动窗口前指定 MuMu 安装目录，或直接指定包含 `adb.exe` 的文件夹。

```powershell
$env:GGET_MUMU_DIR = 'D:\模拟器\MuMu'
python -B original_battle_reuse/battle_ui.py
```

## 本地数据布局

`GGET_BATTLE_DATA_DIR` 指向源码检出之外的目录。省略变量时，程序使用与项目同级的 `<项目目录名>-local`。可以直接指向已有的数据工作区。下列路径相对于该目录。

| 路径 | 本地内容 |
| --- | --- |
| `original_battle_reuse/artifacts/android/` | `bundle.json`、Android guest rootfs 和版本匹配的游戏运行文件 |
| `original_battle_reuse/artifacts/android/rootfs/game/stage/` | 基础关卡 manifest、主表、关卡实体、NPC 校验、原版资源索引和本地部署输入 |
| `original_battle_reuse/artifacts/stages/` 与 `original_battle_reuse/artifacts/stage_update/` | 追加关卡及更新后的主表和本地化 |
| `original_battle_reuse/artifacts/kernel/` | QEMU、内核、每局输入与日志 |
| `original_battle_reuse/artifacts/battle_ui_assets/` 与 `original_battle_reuse/artifacts/idle_koma/` | 可选界面图片、地图与棋子 |
| `original_battle_reuse/artifacts/battle_saves/` 与 `original_battle_reuse/artifacts/formation_saves/` | 窗口生成的存档和编队 |
| `investigations/20261016_update/` | 关卡和资源准备所需的 `parsed_tables.json`、本地化和资源索引 |
| `.resource_cache/` | 原始备份的本地路径索引、metadata 与提取工具所需的其他本地输入 |
| `outputs/battle_headless/` | 已有本地数据目录中的编队捕获、关卡描述、MuMu 检查点副本和可选截图 |

原版依赖需要从自己的数据准备。优先使用已验证的运行目录。下面的准备工具适用于已经具备对应版本本地备份和输入的情况。

1. `inspect_sources.py` 使用 `.resource_cache/latest_game_audit.json` 的 `raw_export` 路径及 `.resource_cache/mapped-metadata-0.dat` 生成 `artifacts/source/source_manifest.json`。备份中的 `APK/` 含 `base.apk` 和 ARM64 split。
2. `prepare_android.py` 根据 manifest 提取运行文件，校验库指纹并准备 Android rootfs。
3. `prepare_stage.py --fetch-bundle` 读取本地备份、提取工具的本地输入、主表快照与 `outputs/battle_headless/eternal_road_hard/stage.json`，准备基础关卡。`prepare_player.py` 从本地编队捕获准备部署输入。只有已有匹配主表、关卡与编队输入时才执行这一步。
4. Windows 独立运行时用 `prepare_kernel.py` 准备 QEMU 和内核。MuMu 路径使用已运行并启用 ADB 的模拟器。

## 运行与诊断

```powershell
python -B original_battle_reuse/battle_ui.py
python -B original_battle_reuse/kernel_runner.py interactive --stage 90510005
python -B original_battle_reuse/export_mumu_snapshot.py --raw-only
```

MuMu 导入前等游戏回到我方阶段的战场地图，在桌面窗口点「从 MuMu 导入战斗」。`--raw-only` 只把最近写盘的检查点复制到外部数据目录。续战随机数在后端初始化。实现、协议和南塔验证结果见 [战斗模拟开发文档](../docs/BATTLE_DEVELOPMENT.md)。
