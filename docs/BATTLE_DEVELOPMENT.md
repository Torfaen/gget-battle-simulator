# 战斗模拟开发文档

## 运行入口

| 用途 | 入口 | 执行的战斗逻辑 |
| --- | --- | --- |
| 桌面手操、MuMu 续战、与实机对照 | [`original_battle_reuse/battle_ui.py`](../original_battle_reuse/battle_ui.py) | 独立 ARM64 进程调用原版 IL2CPP 战斗服务，Python 窗口提交指令并显示事件 |
| 命令行复现部署、交战和阶段切换 | [`original_battle_reuse/kernel_runner.py`](../original_battle_reuse/kernel_runner.py) | 用固定的关卡、编队和脚本驱动同一套原版战斗服务 |

准备 Windows 或 MuMu 运行环境和关卡数据后，从仓库根目录启动。依赖安装与资源准备步骤见 [原版战斗运行时说明](../original_battle_reuse/README.md)。

```powershell
python original_battle_reuse/battle_ui.py
python original_battle_reuse/kernel_runner.py interactive --stage 90510005
```

桌面窗口默认使用 MuMu 中的独立进程。普通新开战斗也支持 Windows 上的 QEMU 路径。原版命令行回归关卡 `90510005` 是永恒之路 Hard「机动战士高达」。`opening`、`exchange`、`phase` 和 `stage-run` 分别用于检查部署和开战、单次交战、阶段推进以及脚本驱动的整关结算。

## 原版后端与手操窗口

1. 关卡准备脚本生成主表、关卡对象和资源索引。编队模块把窗口编队转换为原版用户表，计算驾驶员、支援者与地形属性，并检查机体形态、装备和出击限制。[`prepare_stage.py`](../original_battle_reuse/prepare_stage.py) · [`formation.py`](../original_battle_reuse/formation.py) · [`formation_rules.py`](../original_battle_reuse/formation_rules.py)
2. 命令行运行器准备本局输入。Android 启动器查找 MuMu 的 ADB 连接，校验共用运行文件，并在隔离目录启动客体。客体初始化 IL2CPP、主表和关卡。[`kernel_runner.py`](../original_battle_reuse/kernel_runner.py) · [`android_bridge.py`](../original_battle_reuse/android_bridge.py) · [`android_entry.c`](../original_battle_reuse/android_entry.c)
3. 窗口通过标准输入逐行发送指令。常用命令包括 `state` 查看盘面、`inspect <单位号>` 查看行动选项、`m <单位号> <x> <z>` 移动、`s <单位号>` 待机、`pass` 结束回合和 `undo` 撤回。客体在标准输出发送制表符分隔的 `UI` 事件，`STATE` 和 `UNIT` 描述盘面，`READY` 表示可以接受下一次操作。[`android_interactive.c`](../original_battle_reuse/android_interactive.c)
4. 手操指令驱动原版我方阶段。结束回合后，客体用原版阶段服务和 AI 推进自动阵营，窗口根据后端事件显示行动和受击应对。每局日志位于 `original_battle_reuse/artifacts/kernel/interactive/`，可查看 `original_phase_step`、`original_phase_combat` 和 `UI` 事件，排查行动顺序和状态变化。[`android_exchange.c`](../original_battle_reuse/android_exchange.c)

## MuMu 战斗进度导入

可从国际版 **2.8.0** 最近写盘的我方阶段续战，包括一回合内已经结算多步的盘面。等 MuMu 回到战场地图后，在桌面战斗窗口点「从 MuMu 导入战斗」。游戏检查点还没有 `LatestStep` 时，窗口按开场部署建局。

1. 导出器通过 ADB 复制检查点，核对读取前后的大小、修改时间和 SHA-256，再对副本运行原版 MemoryPack 解码。窗口使用 `read_progress=True` 读取回合、阶段和我方支援者元数据。**命令行默认导出开场部署**，用 `--raw-only` 可单独复制检查点。[`export_mumu_snapshot.py`](../original_battle_reuse/export_mumu_snapshot.py)
2. 元数据读取进程按已校验的 2.8.0 二进制布局解码，跳过依赖战斗仓库的对象重建。续战客体使用同版本的原版反序列化与仓库恢复逻辑，从 `LatestStep` 载入盘面。检查点在阶段首步时补执行一次阶段开始流程，回合中途直接使用已经结算的状态。[`android_snapshot.c`](../original_battle_reuse/android_snapshot.c) · [`android_interactive.c`](../original_battle_reuse/android_interactive.c)
3. 从 APK 提取的库及 metadata 按哈希缓存。2.8.0 客体从本机 MuMu 复制所需 SQLite 主表，在本局目录内使用相对路径打开。Houdini 下曾复现较短的会话目录名导致 SQLite 表打开失败，启动器使用 `<关卡 ID>-<32 个十六进制字符的 UUID>` 作为会话名。
4. 续战后保存的 `.ggetsave` 是 ZIP。它用 `BattleTimeline.bin` 记录后端时间线，`Formation.json` 保留出击编队，`MuMuSupporters.json` 保留两队支援者实例、经验和突破级别。读档时后一个条目用于选择 2.8.0 并重建支援者。旧存档沿用既有读档路径。封包逻辑见 [`battle_save.py`](../original_battle_reuse/battle_save.py)。

### 实机验证与随机数

| 验证场景 | 已验证结果 |
| --- | --- |
| 南塔 9F（`90700309`） | 经窗口导入第 2 回合两次攻击后的检查点。核对两台已攻击机体的站位以及场上 14 个单位的 HP、EN、MP、SP。后端继续手操一次待机，保存并读回成功 |
| 南塔 10F（`90700310`） | 原版 2.8.0 后端分别从第 2 回合开始和回合中途的检查点恢复，接收后续手操指令 |

实机写盘的检查点提供战斗进度。窗口导入后，随机数由后端初始化，日志显示 `REUSE mumu_random_state local`。原生入口还支持配套的 `MuMuRandom.bin`，按回合、阶段和步号校验，恢复 AI、战斗、通用随机流和 Unity 随机状态。实机随机状态采集和跨端敌方行动逐步一致性仍待验证。对照时应先核对同一步的盘面和可行动状态，再用同一组手操指令比较敌方单位、目标、武器、移动终点及结算。

## 开发验证

```powershell
python -B -m unittest discover -s original_battle_reuse -v
```

- 改动窗口、存档或客体协议时，先跑原版后端的 Python 回归，再走一遍窗口的开始战斗、操作、保存和读回。改动导入路径时，用开场、回合首步和回合中途的检查点覆盖各分支。
- 编队回归使用同版本外部主表和部署输入。存档格式测试可以直接在干净检出运行。
- 发布的文件包括源码、文档和随代码运行的测试。实机检查点、账户编队、APK、解包资源和运行日志由 `GGET_BATTLE_DATA_DIR` 管理。目录结构见 [本地数据布局](../original_battle_reuse/README.md#本地数据布局)。
