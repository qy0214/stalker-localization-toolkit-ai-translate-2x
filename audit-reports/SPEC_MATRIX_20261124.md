# 30B-A3B 投机解码矩阵评测（2026-11-24）

环境：i7-12700H（14C/20T）/ 31.7 GB RAM / RTX 3060 Laptop 6 GB（驱动 610.60）
运行库：llama.cpp **b12651**（官方 `llama-b12651-bin-win-cuda-12.4-x64.zip`，
SHA-256 `1a6c090f…620a29` 校验一致；cudart 取自插件 b11612 的 `bin/cuda`）
目标模型：`Hy-MT2-30B-A3B-Q4_K_M.gguf`（18,236,702,880 字节，GGUF 头校验通过）
启动参数：`-c 8192 -ngl 99 --n-cpu-moe 48 --moe-cache-mib 2048 -fa on --parallel 1 --metrics`
测试集：aera `area_dialogs_all_las.xml` / `st_dialogs.xml` 固定种子抽样 40 条（另有 2 条热身）
判分：completion_tokens / 墙钟（含 prompt eval），并记录单条中位数

## 一、结论

1. **三个 1.8B 草稿全部无法用于 30B**，投机草稿方案在本机不成立：
   - Q8 草稿（`Hy-MT2-1.8B-Q8_0.gguf`）：`E spec common_specu: the target and draft
     vocabs are not compatible` / `failed to initialize speculative decoding context:
     draft model vocab type must match target model to use speculation`
   - 2Bit / 1.25Bit 草稿（AngelSlim）：b12651 加载即失败
     `gguf_init_from_reader: tensor 'blk.0.attn_k_norm.weight' has offset …,
      expected …`——依赖 `Q2_0c` 内核（llama.cpp **PR #19357，仍是 OPEN 状态**），
     官方任何 release 都不含该内核。
2. **word 级词表差异是硬约束**：30B-A3B `n_vocab=120832`（arch `hy_v3`，
   eos 120025）；1.8B 全系 `n_vocab=120818`（arch `hunyuan-dense`，eos 120020）。
3. **ngram 类投机（ngram-mod / ngram-simple）实际未生效**：spec 计数器恒为 0，
   `graphs reused` 与无投机组逐条一致（≈1 图/token）。观测到的 ±5%~+52% 波动是
   热降频噪声，不可复现。
4. **30B 单独跑的吞吐在 2.90–10.26 tok/s 之间大幅波动**（中位约 5.4 tok/s），
   同一机器同一参数，长跑后段明显掉速（GPU 过热降频）。按 5 tok/s 估算，
   24 万条目级别的完整 MOD 翻译不具可行性。

## 二、原始数据

| 组合 | 说明 | tok/s | 单条中位 | 备注 |
|---|---|---:|---:|---|
| A_Q8（第一轮，未指定 --spec-type） | 草稿加载但投机未启用 | 10.19 | — | 与 D 同速，属基线 |
| D_none（第一轮） | 无投机 | 10.26 | — | 冷机/页缓存热 |
| A_Q8（第二轮，--spec-type draft-simple） | 草稿词表不兼容 | 4.81 | — | spec 初始化失败，退化为基线 |
| B_2Bit | AngelSlim 2bit | — | — | 加载失败（Q2_0c） |
| C_125Bit | AngelSlim 1.25bit | — | — | 加载失败（Q2_0c） |
| D_none（第二轮） | 无投机 | 5.39 | 5.62s | 同轮基准 |
| E_ngram_mod（第二轮） | ngram-mod | 6.63 | 4.30s | 本轮 +23%，未复现 |
| F_ngram_simple（第二轮） | ngram-simple | 5.45 | 6.19s | 无差异 |
| E_ngram_mod（第三轮） | 顺序 E→D | 6.06 | — | |
| D_none（第三轮） | 顺序 E→D（后跑） | 2.90 | — | 明显热降频 |
| 配对 d1 / e1 | 10 条 | 4.61 / 4.40 | 8.03 / 9.70s | E 反而慢 5% |
| 配对 d2 / e2 | 10 条 | 4.32 / 6.58 | 9.46 / 6.24s | E 快 52% |

配对两轮方向相反 → ngram 无稳定收益。

## 三、证据文件

`C:\models_a3b_test\server_*.log`（每组完整 server 日志）
`tmp/spec_matrix_result.json`、`tmp/pair_d1.json`、`tmp/pair_e1.json`、
`tmp/pair_d2.json`、`tmp/pair_e2.json`、`tmp/eval_spec_matrix.py`

## 四、对插件的含义

- 30B-A3B 需要 `--n-cpu-moe` 等 MoE 卸载参数与 b12651 级运行库，插件当前
  （b11612 + 1.8B/7B 双档）不支持；即便支持，约 5 tok/s 的速度只适合
  抽样翻译/质量对照，不适合整 MOD。
- 想提速只能换更小的同词表模型（1.8B/7B 自成一套），或等 PR #19357 合并后
  再评估 2bit/1.25bit 草稿（且仍需同词表的 30B 系草稿，本机 6 GB 显存也放不下）。
