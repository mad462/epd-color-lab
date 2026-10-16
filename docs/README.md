# 3.7″ 墨水屏 48 色 · 文档索引

## 先看这个

👉 **[颜色方法论.md](颜色方法论.md)** —— 颜色怎么测、偏在哪、怎么评估、最后用了什么方法，
以及**走过的 8 条弯路**（每条都有症状、原因、和推翻它的数据）。

调色或重新测量之前，先读这份。

---

## 当前有效的数据

| 文件 | 说明 |
|---|---|
| `palette_qual_measured.csv` | **实测原值，唯一事实来源**。所有量化、预览、`.act` 都以它为准 |
| `palette_qual.csv` | 标称值（FrameFilm 的设计意图），只作对照 |
| `palette_fast.csv` | 2 相标称值，**尚未实测** |
| `../main/palette_table.c` | 固件色板表。**必须与 `../host/act/48-color-qual.act` 逐字节一致** |
| `../host/act/48-color-qual.act` | 192 字节色板（= 实测值） |
| [面板对比_3.7_7.3_7.09.md](面板对比_3.7_7.3_7.09.md) | 三块面板能不能刷 48 色，为什么 |

### 三个地方必须同步

改色板时要同时更新，否则固件认不出 BMP 用的哪张色板：

```powershell
python ..\host\make_act.py --act-dir <QUICKSHAKE_DIR>\act --qual-csv palette_qual_measured.csv
python ..\host\make_act.py --act-dir ..\host\act                        --qual-csv palette_qual_measured.csv
python ..\host\make_palette_table.py --fw-dir ..\main                    --qual-csv palette_qual_measured.csv
```

---

## ⚠️ 不要用这个

| 文件 | 为什么 |
|---|---|
| `废弃_Lstar归一化色板.csv` | 把色板 L\* 拉到 [0,100] 的产物。**暗部色度被抹平，用它量化会导致暗部偏红**。见方法论第六节弯路 1 |

名字里的「废弃」是故意的 —— 防止有人看到 "calibrated" 之类好听的词就拿去用。

---

## 历史记录（保留过程，结论可能已过时）

| 文件 | 内容 | 状态 |
|---|---|---|
| `跑通48色记录.md` | 第一次跑通 48 色的完整记录（波形写入协议、时序实测） | 有效 |
| `色板说明.md` | FrameFilm 色板的来源与结构 | 有效 |
| `实测分析.md` | 第一轮实测分析 | 有效，数据已被方法论收录 |
| `校准说明.md` | 校准流程说明 | 部分过时：固件现在**开机直接进校准模式**，不用敲 `c` |

---

## 渲染对比图（调参时看这些）


**这些图都是用实测色板渲染的**（= 面板实际显示的颜色）。
用标称值渲染出来的是"理想效果"，会骗人。

重新生成：

```powershell
python ..\host\preview_variants.py "图片.png"
python ..\host\sweep_params.py     "图片.png"
python ..\host\sweep_blend.py      "图片.png"
```

---

## 工具脚本

| 脚本 | 作用 |
|---|---|
| `../host/palette_report.py` | 生成方法论文档里的数据表 |
| `../host/preview_variants.py` | 管线分段渲染对比 |
| `../host/sweep_params.py` | 参数扫描 |
| `../host/sweep_blend.py` | 色板混合扫描 |
| `../host/analyze_cal.py` | 分析实测数据（输出废弃色板 + 重复色统计） |
| `../host/make_act.py` | 生成 `.act` |
| `../host/make_palette_table.py` | 生成固件色板表 |
| `../host/server.py` | 本地助手：网页传图 / 读设备列表 / 切图 / 进度 |
| `../host/device.html` | 传图台页面 |
| `../host/check_page.py` | **改完页面必跑** —— 交叉核对 JS 引用的 id |

---

## 一句话总结这套方法的教训

**平均色差小 ≠ 观感好。**

- ΔE2000 说"白平衡方案最好"（5.98），上屏却明显偏暗 —— 平均值掩盖了全局明度偏移
- 用户说"偏黄"，量出来 b\* 却是 **-1.5（偏蓝）**，真问题是 **a\* 掉了 7.3（红润丢失）**

**最终起决定作用的方法：用实测色板把结果渲染出来，亲眼看。**
