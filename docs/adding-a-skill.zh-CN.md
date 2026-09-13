# 一个 skill 在本仓库里是怎么运作的

*[English](adding-a-skill.md) | 简体中文*

[CONTRIBUTING.md](../CONTRIBUTING.md) 是清单：一个技能必须交付哪些文件。
本文讲的是**为什么** —— 这套机制到底拿这些文件做了什么，这样当某个检查失败时，
你知道那个分数是哪一个环节产生的。

加第一个技能之前读一遍。之后看 CONTRIBUTING 就够了。

## 设计原则：确定性工作交给脚本和工具

**成本控制要从编写 skill 时开始考虑。让模型理解意图、做语义决策；把规则明确、
可重复执行的工作交给确定性的脚本或工具。** 这样既能减少模型调用、token 消耗和
重试，也让结果更容易复现和验证。

写 `SKILL.md` 之前，先明确每一步由谁执行、留下什么证据：

| 工作 | 执行者 | 证据 |
|---|---|---|
| 理解需求，选择组件和关系 | 模型 | 简洁的结构化规格 |
| 解析输入、校验 schema、查找已知标识符 | 脚本或工具 | 校验后的数据或具体字段错误 |
| 计算布局、数量、分数、帧数和时长 | 脚本或工具 | 计算值及明确的检查结果 |
| 生成 XML/HTML、转换格式、渲染文件 | 生成器或渲染器 | 产物路径和验证报告 |
| 检查文件完整性、尺寸、必填字段、输入是否保持不变 | 校验器 | 实测值、具名检查项，必要时附哈希 |
| 判断结构检查无法覆盖的语义、表达和视觉质量 | 必要时使用模型 | 基于产物和实测结果的判断依据 |

例如画流程图时，模型负责在 JSON spec 中决定节点、连线和动画步骤；渲染器负责
计算帧数和时长，校验器负责检查几何约束和背景是否保持不变。把这些结果传给模型
评审，不要再让模型数帧或从截图猜文件完整性。结构验证通过仍不代表视觉质量合格。

工作流需要明确：

- **输入未变时复用结果。** 只读相关参考内容，安装前检查依赖，复用有效的中间
  产物；输入、配置或工具版本变化后，要使旧结果失效。
- **按具体错误修正。** 昂贵的生成步骤前先校验输入。工具返回字段名和失败检查项，
  让 agent 只修正相关输入。设置重试上限，耗尽后明确报告错误并停止。
- **保留简短而完整的证据。** 保存机器可读报告，返回带有检查名称、实测值和文件
  路径的摘要。确保评估器能读取完整报告，关键证据不能只存在于可能被截断的长日志
  末尾。
- **检查代表性用例的成本。** 查看工具调用数、模型输入/输出 token、重试次数和
  耗时。为任务设置适当的运行限制，用 `expect.max_tool_calls` 检测工作流退化。
  工具调用数上限不等于金额预算；token 数量和模型价格也决定费用。

评估也遵循这个原则：先用固定样例检查脚本，再运行受影响的用例。阶段 1 虽然使用
确定性 grader，执行 agent 本身仍会调用模型。需要语义或视觉判断时再增加模型
评审；只修改 judge 时，复用兼容的执行记录重新评分，无需再次运行 agent。
重新评分仍会产生模型费用。保留正确性门禁，查清失败原因，不要靠反复运行碰到
一次通过。

---

## 1. 目录结构

技能就是 `skills/` 下的一个目录。没有任何注册步骤；加载器靠遍历目录发现它。

```
skills/my-skill/
  SKILL.md                     必需。frontmatter 里要有 `name` + `description`
  scripts/                     可选。技能让 agent 去执行的东西
  references/                  可选。技能可能让 agent 去读的背景资料
  examples/                    可选。格式样例
  eval/
    dataset.jsonl              必需。测试用例
    plugin.py                  可选。你自己的评估器（第 2 层）
    negative-control/          有模型裁判时建议提供；--strict 检查覆盖
```

发现机制（`evalkit/sandbox.py:165`、`evalkit/plugins.py:200`）带来两条约束：

- **`SKILL.md` 必须存在**，否则这个目录对沙箱来说是不存在的。
- **frontmatter 里的 `name` 才是技能的身份**，不是目录名。它是 agent 必须传给
  `Skill` 工具的字符串，也是你数据集里 `expect.skill` 必须匹配的值。让两者保持
  一致，可以省掉一类没人愿意调试的困惑。

`eval/` 有意放在技能旁边：这样技能的 git sha 同时标定了它的**行为**和它的
**评分标准**，任何分数变化都能归因到某一次提交。

---

## 2. Agent 看到的是什么

这是最容易让人意外的部分，也决定了 `SKILL.md` 该怎么写。

**`SKILL.md` 不在 system prompt 里。** 沙箱只注入一份 `(name, description)`
目录，和 Claude Code 的 available-skills 列表完全一样：

```xml
<available_skills>
  <skill>
    <name>my-skill</name>
    <description>……你 frontmatter 里的 description……</description>
  </skill>
</available_skills>
```

所以 agent 必须仅凭一段描述**选中**你的技能，然后才能加载它。这正是
`SkillSelectionAccuracy` 成为一项真实测量而不是形式主义的原因——也意味着
**`description` 是承重的**。它是你的技能和"被跳过"之间唯一的东西。把它写成触发
条件（"Use when the user asks to …"），而不是写成一段概述。

**Agent 的工具箱是刻意做小的**（`sandbox.py:427`）：

| 工具 | 说明 |
|---|---|
| `Skill(skill)` | 返回你的 `SKILL.md`。工具名和 `skill` 参数名是协议契约，见 §6 |
| `Read(path)` | 工作区，或只读地访问技能注册目录 |
| `Write(path, content)` | 仅工作区 |
| `LS(path)` | |
| `Bash(command)` | 以工作区为 cwd 运行。你的 `scripts/` 就是这样被执行的 |

**没有任何办法向用户提问。** 一份写着"你必须先与用户确认再定稿"的 `SKILL.md`，
每次都会因为跳过了 harness 根本做不到的步骤而得 0.50 —— 而在没有提供元数据的
运行里，它会卡在提问上，最终什么交付物都没写出来。把交互写成条件性的：有用户在
场时就问，没有时就按最佳猜测出稿并打上 `_(to confirm)_` 标记。

**`Skill` 被调用时，整个技能目录会被复制进工作区**（`sandbox.py:194`）。
`SKIP_DIRS` 里的目录 —— `node_modules`、`.git`、`__pycache__`、`.venv` ——
不复制，而是软链回注册目录，所以庞大的依赖树成本很低。两个后果：

- 你 `SKILL.md` 里所有相对路径都相对这份副本解析，副本位置由工具返回值告知
  agent。写 `<skill-dir>/scripts/…` 并定义一次 `<skill-dir>`，不要硬编码路径。
- 每个被复制的文件都会**记录基线哈希**。之后收集产物时会跳过哈希未变的文件，
  所以技能自身的源码永远不会被当成 agent 的输出。但 agent **修改过**的文件会
  出现在产物里。

---

## 3. 两个阶段

```bash
# 阶段 1 —— 执行、记录
python evalkit/run_eval.py --target local

# 阶段 2 —— 给这份记录打分
uv run --with strands-agents-evals python evalkit/run_strands_eval.py
```

两个阶段保持分离，是为了复用 agent 的执行记录。重新评分仍会调用付费 judge，并可能
受模型采样、评分标准、参考文件和依赖影响；它不是确定性的纯函数。两个阶段现在共用
项目的 uv 环境。

上面是底层入口。日常推荐 `uv run --locked skill-eval run --skill <name>` 自动预检、执行、
评分，用 `skill-eval score --run <id>` 复用归档。详见[目录与归档结构](architecture.md)。

### 阶段 1，逐步

1. 加载所有 `skills/*/eval/dataset.jsonl`（或 `--cases`、`--only <id>`）。
2. 每个用例：全新临时工作区 → 写入 `seed_files` → 运行 agent。
3. **重试瞬时性 Bedrock 故障** —— 限流、流中断 —— 最多 3 次
   （`run_eval.py:72`）。网络故障不能被上报成技能缺陷。
4. **工具预算在进入时扣减，而不是在完成时**（`sandbox.py:90`），这样超限的调用
   不可能还把文件写出去。它以 `BaseException` 抛出，因为 Strands 的 `@tool`
   包装器会吞掉 `Exception` 并当作可重试错误交回模型 —— 实测中，预算设为 2 的
   一次运行做了 51 次调用，并且仍然报告 `status: ok`。
5. **收集产物**：工作区里所有哈希与基线不同的文件。≤ 256 KB 的文本会被内联，其他收集文件的原始字节保存到磁盘。
6. **按 `expect.*` 做确定性打分**（§4）。
7. 写 `results.json`，并把每个产物存到 `artifacts/<case-id>/`。这个目录会
   **先被清空** —— 否则上一轮的文件会和本轮的混在一起，读 `artifacts/` 时会把
   两次运行当成一次。

只有当所有用例通过所有确定性检查时，退出码才是 0。

### 阶段 2，逐步

1. 把 `results.json` 和数据集做 join —— 记录说明发生了什么，数据集说明期望什么。
2. 把轨迹重建为 Bedrock 风格的消息列表（`trajectory.py`）。
3. **把从磁盘读回的文本交付物追加为最后一个 user turn**
   （`run_strands_eval.py:113`）。这是必需的：沙箱把每个字符串型工具参数截断在
   400 字符，所以一次写入 2.7 KB markdown 的 `Write` 看起来是被截断的 —— 一个
   要判断"有没有总计行"的裁判会诚实地说它看不出来，而那个文件其实有。默认包含
   `.md`、`.mmd`、`.txt`、`.csv`；其他文本格式通过 `expect.text_artifacts` 覆盖。
4. 调用你的 `plugin.applies(expect)`；为真则调用 `plugin.prepare(ctx)`（§5）。
5. 第 1 层 + 你的第 2 层放在同一个 `Experiment` 里并发执行。
6. 写 `eval-report.json`。任何**门禁行**失败、或实际打分的用例数少于请求数，
   都以非零码退出 —— 否则阶段 1 在最难的用例上崩溃，反而会**拉高**总分并且
   仍然退出 0。

---

## 4. `dataset.jsonl` —— 契约

一个技能需要多少用例就写多少个，可以每行一个对象，也可以缩进成多行——
`evalkit/dataset.py` 把整个文件当作 JSON 对象流来解码，两种写法都行，顶层是
一个数组也行。一旦用例里带上散文式的 `assertions` 或者好几 KB 的 `seed_files`，
就用缩进写法：一个必须挤在一行里的用例，是一个改之前没人会重读的用例。
`//` 开头的行会被跳过（替换为空行，所以报解析错误时给出的行号仍与你编辑器里
看到的一致）。

```jsonc
{
  "id": "three-tier-web",
  "prompt": "Draw a standard three-tier web application on AWS …",
  "seed_files": {"input.txt": "…"},
  "expect": { /* 见下 */ }
}
```

### 顶层字段

| 字段 | 含义 |
|---|---|
| `id` | 用例 id。决定 `artifacts/<id>/` 目录名；在**所有技能之间**必须唯一 |
| `prompt` | 用户输入 |
| `seed_files` | `{相对路径: 内容}`，在 agent 运行前写入工作区。值也可以是 `{"from": "<相对 dataset 的路径>"}`，表示从磁盘读一份 fixture 而不是内联 |

对抗性用例故意让用户指令与技能指令冲突 —— 用户说"直接把 XML 给我，别跑脚本"，而技能说
必须运行生成器。阶段 2 里没有任何评估器衡量对**用户**的忠实度，所以这类用例不需要特殊
处理：`SkillInstructionFollowing` 拿技能自己的 `SKILL.md` 当评分标准，而在压力下守住规则
正是它奖励的行为。

### `expect.*` —— 确定性打分器

所有字段可选；缺失的字段会被记为 "no expectation" 并通过。

| 字段 | 打分器 | 何时失败 |
|---|---|---|
| `skill` | `skill_selected` | 指定技能从未被加载 |
| — | `no_wrong_skill` | 加载了**任何其它**技能。无条件运行，没有对应字段 |
| — | `completed` | 运行出错或耗尽工具预算。无条件运行 |
| `file_glob` | `file_produced` | 产出文件中没有匹配该 glob 的 |
| `validator_pass_regex` | `validator_passed` | 该模式在任何 `Bash` 输出中都不出现 |
| `handwrite_marker` | `generator_not_bypassed` | 某次 `Write` 的内容匹配该模式 |
| `max_tool_calls` | `tool_budget` | 调用次数超过上限 |
| `tools_any_order` | `tools_used` | 每个工具一行；未被调用的工具那一行失败 |
| `tools_in_order` | `tools_used` | 该序列不是轨迹的**子序列** |
| `artifact_regex` | `artifact_regex` | 某个模式在产物文本中 0 匹配 |
| `artifact_regex_absent` | `artifact_regex` | 某个模式匹配 ≥ 1 次 |
| `min_counts` | `count_at_least` | `{模式: n}`；匹配少于 `n` 次 |
| `text_artifacts` | — | 哪些扩展名算作给裁判的可读证据。默认 `.md .mmd .txt .csv`；**如果你的交付物是 `.json` / `.yaml` / `.html` / `.sql`，必须设置它**，否则裁判是盲判的。传 `[]` 表示不提供 |
| `assertions` | `Assertions`（第 1 层，模型裁判） | **每一条**都会被逐条判定，各占报告里一行（`Assertions[#1]`、`Assertions[#2]`……），任何一条不成立就让整次运行失败。写法见下文 |
| *（你自己的字段）* | 你的插件 | 例如 `reference_image`，由 `aws-drawio-diagram` 和 `visual-flow-webp` 两个插件读取。**这是插件约定，不是 SDK 字段** —— 见下文 |

写第一个用例之前值得知道的几个行为：

- **一个用例只对应一个技能。** 多技能评测不在范围内：没有 `expect.skills` 列表，
  第 2 层只按 `expect.skill` 解析出唯一一个插件，而且 `no_wrong_skill` 会让"加载
  了第二个技能"的运行失败——即使第一个选对了。如果一个任务确实需要两个技能，
  拆成两个用例。
- **`tools_in_order` 是子序列匹配，不要求相邻**（`tool_trajectory.py:41`）。
  技能理所当然会在规定步骤之间读一个参考文件，要求严格相邻会让一个"做完了所有
  要求、外加一个合理步骤"的运行失败。
- **构造期望轨迹时，`tools_in_order` 会回退到 `tools_any_order`**
  （`trajectory.py:119`）。只声明 `any_order` 的用例，仍然会被顺序类
  评估器读到。
- **`handwrite_marker` 抓的是最常见的静默违规**：模型用 `Write` 手写产物，而不去
  运行你的生成器。输出看起来可能没问题，但技能被完全忽略了。选一个只有你生成器
  的输出才含有的字符串 —— drawio XML 用 `mxGraphModel`。
- **`artifact_regex` 作用在文件的*文本*上**。二进制产物没有文本，内容正则在那里
  毫无意义；结构性检查请放到插件里。
- 如果你的技能自带校验器，**`validator_pass_regex` 是单项信号最强的检查**：
  它证明规定路径被**执行**了，而不只是被描述了。
- **"你的技能不该被选中"的用例，属于另一个技能的数据集**，并在那里用
  `expect.skill` 指明那个技能。

### 怎么写 `assertions`

一条 assertion 是关于交付物的散文式断言，正则表达不出来的那种：「500 条的上限
**以及**为什么调高会失败，两者都被记下来了」「固定的版本号是以约束的形式出现，
而不是泛泛的建议」。每个用例一次裁判调用，一次判完全部断言，并**按序号**对齐
返回的判定；缺少判定的那一条按失败计，而不是把后面的判定挪到错误的断言上
（`evalkit/assertions.py`）。

由判定方式推出四条规则：

- **只写工具链能看到的文本** —— 轨迹，以及追加进轨迹的文本交付物（见
  `expect.text_artifacts`）。关于像素或二进制结构的断言在这里无法验证，
  该放进 `eval/plugin.py`，那里拿得到文件本身。
- **判定是二元的。** 故意不提供「无法判断」这一档：留了后门，恰恰会在最该守门
  的断言上被走掉，而且那种行会被从均值里剔除。
- **复合断言必须每一部分都成立**，裁判被要求指出缺的是哪一部分。通常这正是你想
  要的——但如果你更希望看到是哪一半失败，就拆成两条。
- **该说「而不是」的时候要说。** 「X 被记为约束」只要 X 出现在任何地方就算满足；
  「被记为约束而不是泛泛的建议」不是。

含糊的断言就是等着发生的裁判噪声。实测：「文件里没有为列举而列举的文件清单」这
一条，在产物完全没变的情况下两次运行给出了不同结论——文件里有一份带注释的四行
文件清单，两种判法都说得通。如果某一行无缘无故地变了，先去磨那句话，再去怀疑
裁判。

### `reference_image` —— 是约定，不是 SDK 字段

`strands_evals` 里不存在 `reference_image` 这个名字。SDK 提供的是
`MultimodalInput(media=[ImageData(source=…, format="png"), …])`，而"参考图"不过就是
**这个列表里的第一张图**。其余全是数据集字段与读它的插件之间的按技能约定 ——
`aws-drawio-diagram` 和 `visual-flow-webp` 各用约 20 行实现了它：

1. `_reference_image(expect)` 把 `expect.reference_image` 相对该技能的 `eval/` 目录
   解析。**声明了但文件不存在时返回一个 problem，而不是 `None`** —— 静默放过是看不见
   的：用例会退回普通 rubric、依然通过，而且没有任何地方说"这次比较没跑"。drawio
   技能就这样静默了很多次运行，直到有人注意到行名不对。
2. `prepare` 把参考图插到 `media[0]`，并记下 `metadata["reference_path"]`。
3. 指令里必须写明"图 1 是参考，图 2 是被评对象"—— prompt 组装器输出的媒体块没有
   任何标题，而且文字是**追加在所有图片之后**的，除此之外没有任何东西能区分两张图。
4. `evaluators` 给 rubric 追加 `REFERENCE_COMPARISON` 段，并把行名改成 `…[vs reference]`：
   带参考打的分和不带参考打的分回答的是不同问题，不能混在一起平均。

**永远不要把图片放进 `expected_output`**：那个字段会被插值进文本
（`f"<ExpectedOutput>{…}</ExpectedOutput>"`），裁判收到的是一个*文件路径*的 pydantic
repr，然后它会流畅地给一张它从未看过的图打分。

参考图有两种用途，还有一种是伪的。**主题参考**（按 case，"这是对*这个*请求的好答案
吗"）放进主视觉裁判的 media。**风格参考**（全局，"符合房屋风格吗"）该单独走一个
`ExtraExperiment`——drawio 就是这么做的，用一张 AWS 官方画的架构图，且不设门禁。而当
渲染器本身就属于你的技能时，风格参考是没有意义的：两张图出自同一段代码，配色和字体
按构造就完全一致，不含任何信号。`visual-flow-webp` 的 `REFERENCE_COMPARISON` 把这一点
直接告诉裁判，并把比较范围限定在构图、覆盖度和疏密——也就是 spec 作者真正决定的东西。

---

## 5. `eval/plugin.py` —— 第 2 层

第 1 层（技能选择、指令遵循、工具轨迹、用例的 `assertions`、`expect.*` 打分器）
由框架拥有，你不需要
任何配置。它对你的技能产出什么一无所知，这正是同一套裁判能在图表生成器和会议
纪要技能之间原封不动复用的原因。

第 2 层是框架无从得知的一切：图里的节点是否落在正确容器内、成本表的算术是否
成立、纪要是否以结论开头。三个可选函数：

```python
from evalkit.plugins import ExtraExperiment, PrepareContext, Prepared

def applies(expect: dict) -> bool:
    """你这一层是否适用于这个用例？"""
    return (expect.get("file_glob") or "").endswith(".drawio")

def prepare(ctx: PrepareContext) -> Prepared:
    """渲染、测量、组装。派生文件写到 ctx.artifacts_dir 下。"""
    return Prepared(findings=[...], media=[...], instruction="...", metadata={...})

def evaluators(prepared: Prepared, judge_model) -> list:
    return [MyEvaluator()]
```

同目录的兄弟模块直接 import（`import geometry`）；加载器会先把该目录放到
`sys.path`。

**如果你的评估器是每个条目产出一个 `EvaluationOutput`** —— 每个节点、每行表格、
每条规则一个 —— 在它上面设 `expand_rows = True`。否则框架会在写报告之前把这些
输出平均成一行，读者只看到 `MyEvaluator 0.71`，看不到是哪个节点错了。加上这个
标记后，每个输出各占一行，行名取自输出的 `label`，形如 `MyEvaluator[<label>]`。
门禁语义不变：聚合行本来也只有在全部输出都通过时才算通过。

**`applies` 为什么重要。** 对本来就不该产出你这类产物的用例返回 `False`。
否则"因为没人要求所以没产出"和"本该产出却没有"会塌缩成同一个 not-applicable
行，整个套件会报告它其实并不存在的失败。

**`prepare` 为什么要和 `evaluators` 分开。** 渲染是你的问题，不是框架的问题。
draw.io 需要 Electron 二进制，Mermaid 需要 mermaid-cli，一张 markdown 表格什么
都不需要。把渲染器塞进框架，会让每个团队为了一种自己从不产出的格式背上依赖。

### 按成本从低到高排列你的层

`aws-drawio-diagram` 就是范例：

| 层 | 成本 | 是否门禁 |
|---|---|---|
| `geometry.py` → `DiagramLayoutEvaluator` | 纯算术，无模型、无网络 | **是** |
| `render.py` + 视觉裁判 | 一次 Electron 导出 + 一次多模态调用 | 是 |
| 与官方 AWS 参考架构做风格对比 | 再一次多模态调用 | **否**（`gate=False`） |

**把你的确定性结论通过 `Prepared.findings` 交给模型裁判。** 让视觉模型去重新
推导一件算术早已确定的事，它有时会算错，而且总要多花一个来回。

**渲染器缺失是工具缺失，不是技能失败。** `render.py` 返回 `(None, reason)` 而不
抛异常，插件退化为只做几何检查，并把原因放进 `findings`。一台没装 draw.io 的
笔记本，不该看起来像一个不会排布图表的技能。

### 两个一定会咬到你的坑

**图片属于 `media`，绝不能放 `expected_output`。** prompt 组装器会把那个字段
插值进文本，所以放在那里的图片到裁判手里变成一个**文件路径的 repr** —— 而裁判
会流畅地给一张它从未见过的图打分。

**一个 `Experiment` 只有一个 `case.input`。** 里面每个评估器看到的输入都相同。
如果你需要第二组图片对（比如与风格参考做对比），请返回 `ExtraExperiment`，
不要往现有 media 列表里追加。裁判并不能可靠地忽略一张"叫它忽略"的图片；这个
泄漏是实测出来的，不是假设。

### 门禁（gating）与仅记录（tracked）

确定性检查为构建把关：它们本质上是算术，在 drawio 技能的六次迭代中，89 项检查
89 次都没有在没有真实原因的情况下发生变化。

模型裁判的行则噪声更大。在同一份产物上、代码毫无改动的情况下实测：同一类缺陷
拿到过 0.72 和 0.85；仅仅改一个标签，分数从 0.82 变成 0.97。一个会被这种量级
噪声触发的门禁，会在第三个团队采用这套框架时被直接关掉 —— 并且会把那些值得信任
的检查一起拖下水。所以将此类评估器放在 `ExtraExperiment(gate=False)` 中：整个额外评分照常上报和打印，但不计入
退出码。**框架不会从裁判的名字去推断这一点；由你显式声明。**

### 负向对照（negative control）

如果你的插件包含模型裁判，就要提供一份**故意做坏**的产物，并在
`negative-control/expected.json` 里声明它的分数上限。

**为什么建议提供。** 技能和给技能打分的标准都由你拥有，所以你可以通过放松尺子
来提高分数 —— 而且当时不会觉得是在作弊。实测：这里有一个评分标准从 0.32 涨到
0.92，其中只有大约一半是技能真的变好了，剩下一半是评分标准长出了一张带锚点的
打分表。负向对照就是区分这两者的办法。如果它在某次改标准之后越过了上限，
说明尺子动了。

**用你真正的生成器、喂一份坏输入来生成这份坏产物。** 手工把文件改烂做出来的
对照，会在到达裁判之前先被你自己的校验器拒掉 —— 那叫"不可测"，不叫"坏"。

---

## 6. 不能重命名的协议契约

下面这些看起来像风格选择，其实不是。每一条都是实测确立的；每一条被违反时都是
**静默失败**，也就是最贵的那种。

| 契约 | 位置 | 违反后果 |
|---|---|---|
| 工具名必须正好是 `Skill`，参数名正好是 `skill` | `sandbox.py:275`、`trajectory.py:37` | 所有 skill 级评估器都会看到一个"什么都没加载"的 agent。`load_skill`、`SkillTool`、或 `{"name": …}` 形式的载荷全都不可见 |
| system prompt 里的 `<available_skills><skill><name>…` 块 | `sandbox.py:227` | 评测 harness 靠正则回收目录。写成 `- name: description` 会被当成散文解析，目录变空，选择裁判在看不到备选项的情况下给这次选择打分 |
| 工具返回值里放完整 `SKILL.md` 正文，而不是被截断的 `result_head` | `trajectory.py:54` | `SkillInstructionFollowing` 会拿指令的一行摘要去判断遵循程度 |
| replay task 的返回键必须是 `output` / `trajectory` | `run_strands_eval.py:313` | `actual_output` / `actual_trajectory` 会被静默忽略，所有 skill 评估器都报 "no trajectory provided" |
| 裁判模型 id 必须是完整的 inference profile | `run_strands_eval.py:76` | `global.anthropic.claude-sonnet-4-5` 看着很像却会被拒，表现为所有裁判以 `ValidationException` 得 0.00 —— 也就是把一个配置错误显示成技能失败 |

---

## 7. `SKILL.md` 同时也是评分标准

`SkillInstructionFollowing` 把你的 `SKILL.md` 当作契约，检查 agent 是否照做。
你不需要另写一套打分规范，因为你已经写过了。

它的副作用值得内化：**自相矛盾会扣分。** 一次正确遵循规则 A 的运行，会因为违反
与 A 冲突的规则 B 而被扣分。实测：新增一条与三节之前的条件性规则相冲突的无条件
规则，让三次**正确**的运行从 1.00 掉到 0.75。Agent 是对的，文档是错的。

对怎么写它的实际影响：

- **让条件性规则看起来就是条件性的。** 不要写"把 XML 贴进回复"，要写
  "**如果用户要的是 XML**，就贴出来 —— 否则报告文件路径"。
  `skills/aws-drawio-diagram/SKILL.md:44` 留着做错这件事的伤疤。
- **不要要求 harness 做不到的事**（§2）。
- **说清楚某一步是前置条件而不是步骤。** "如果 `node_modules/` 已存在就跳过" ——
  否则运行会去重装本来就有的包。
- **提醒你自己目录里布下的陷阱。** 文件名和请求撞车的 `examples/` 文件是经典
  案例：因为用户说了 "Coinbase low latency" 就把
  `examples/coinbase-low-latency.json` 喂给生成器，产出的是**那个样例的**架构图，
  而且下游每一项检查都会通过，因为样例本身是良构的。

---

## 8. 添加一个技能，按顺序

1. `mkdir -p skills/my-skill/eval`，写 `SKILL.md`，frontmatter 里放
   `name` + `description`。description 写成触发条件（§2）。
2. 加上技能需要的东西 —— `scripts/`、`references/`、`examples/`。如果有 npm/pip
   依赖，用 `uv run --locked skill-eval setup --skill my-skill` 安装；
   系统命令写入 `eval/dependencies.json`，见 CONTRIBUTING。
3. 写 `eval/dataset.jsonl`：每一种独立行为至少一个用例。**先只写确定性的
   `expect.*`**，无需领域插件；完整 CLI 仍运行共享 judge，只有阶段 1 不调用 judge。仅这一步就能抓住"技能未被选中"、
   "产物未生成"、"生成器被绕过"。
4. 运行 `uv run --locked skill-eval run --skill my-skill --only <你的用例>`。
   检查失败原因，修正后按相关变化决定是否重跑；验收需要执行和评分门禁通过。
5. 修改 judge 时用 `uv run --locked skill-eval score --run <run-id>` 复用记录。
   阅读已保存的 `SkillInstructionFollowing` 推理，确认它认为跳过了哪一步。
   重新评分仍调用模型，但能避免重复运行 agent。
6. **只有在第 1 层无法表达关键点时**，才加 `eval/plugin.py`。先加确定性评估器，
   模型裁判放最后。
7. 如果加了模型裁判，就要提供带上限的 `negative-control/`，并在每次改评分标准
   之后重新检查它。
8. 在 README 的 Skills 表格里加一行。

### 清单

- [ ] 确定性步骤使用脚本或工具，有明确输入和可验证的输出
- [ ] 已写明复用与重试条件，并检查代表性用例的成本指标
- [ ] `SKILL.md` frontmatter 有 `name` + `description`
- [ ] 没有规则互相矛盾，也没有规则要求向用户提问
- [ ] `eval/dataset.jsonl` 每种独立行为一个用例，`id` 唯一
- [ ] `run_eval.py --only <case>` 通过所有确定性检查
- [ ] `run_strands_eval.py --only <case>` 退出码 0
- [ ] 如果有模型裁判，负向对照已就位且低于上限
- [ ] 可选的高噪声评估放入 `ExtraExperiment(gate=False)`；主 Experiment 的行始终门禁
- [ ] README Skills 表格已更新

---

## 9. 出问题时去哪里看

| 症状 | 可能原因 |
|---|---|
| `skill_selected` 失败 | 你的 `description` 读起来不像触发条件。agent 从未见过 `SKILL.md` |
| `no_wrong_skill` 失败 | 两个 description 重叠。收窄其中一个，并给**另一个**技能的数据集加一个选择类用例 |
| `validator_passed` 失败但产物看着没问题 | agent 描述了你的脚本却没运行它。去 trace 里找 `Bash` 调用 |
| `generator_not_bypassed` 失败 | agent 用 `Write` 手写了产物。通常是 `SKILL.md` 先解释了格式、再才要求用生成器 |
| 所有裁判以 `ValidationException` 得 0.00 | `JUDGE_MODEL_ID` 有问题 —— 用了裸的系列名而不是完整 inference profile（§6） |
| 换到 OpenAI 兼容端点后所有裁判得 0.00 | 端点或模型不支持 tool calling。裁判是以工具的形式索要判定结果的，不是 JSON mode —— 见 `evalkit/models.py` |
| 某个第 2 层的行意外变成 `NOT_APPLICABLE` | `applies()` 返回了 `False`，或 `prepare()` 找不到产物。它的 `findings` 会说明是哪种 |
| 裁判给一个它从未见过的东西打了分 | 图片放进了 `expected_output` 而不是 `media`（§5） |
| 第 2 层分数跳了，但你没改技能 | 你改了评分标准。在相信这个数字之前先看负向对照 |

当前覆盖边界：负对照不是所有技能都有；普通 check 给出警告，strict 会将覆盖警告视为失败。
主 Experiment 的所有评分行都参与门禁，`ExtraExperiment.gate=False` 作用于整个额外评分。
完整 CLI 的报告会记录视觉媒体数量、额外评分数量及插件 findings，阅读时应检查是否有渲染被跳过。
