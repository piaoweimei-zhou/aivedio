# 可清理清单报告（generated 产物）

- 生成时间：2026-08-20 18:22:12
- 判定依据：`assets/asset_registry.json` 中 302 个 asset 的 `urls` 是否引用该文件
- 被采纳（asset 引用）：**230 个**，553.6 MB
- **可清理（孤儿/废图，无引用）：36 个**，57.3 MB

## 一、汇总（可清理项按子目录）

| 子目录 | 可清理数 | 可释放(MB) |
|---|---|---|
| global | 36 | 57.3 |

## 二、可清理文件清单（共 36 个）

```text
global\concept\global_concept_029_f61467.png  (1616 KB)
global\concept\global_concept_031_48cdcd.png  (2224 KB)
global\concept\global_concept_032_fe53ad.png  (2193 KB)
global\concept\global_concept_033_973b41.png  (2101 KB)
global\concept\global_concept_034_74c866.png  (2189 KB)
global\concept\global_concept_035_b6ee90.png  (2022 KB)
global\concept\global_concept_036_21b9e7.png  (2168 KB)
global\concept\global_concept_037_02ddbf.png  (2212 KB)
global\concept\global_concept_038_36f398.png  (2307 KB)
global\concept\global_concept_039_cf6dcf.png  (2006 KB)
global\concept\global_concept_040_306212.png  (2131 KB)
global\concept\global_concept_041_18bbdd.png  (2254 KB)
global\concept\global_concept_042_11c7e4.png  (2229 KB)
global\concept\global_concept_043_13dd4f.png  (2142 KB)
global\concept\global_concept_044_ac872f.png  (2153 KB)
global\concept\global_concept_045_d26df5.png  (2042 KB)
global\concept\global_concept_046_ea50a8.png  (2162 KB)
global\concept\global_concept_047_db7c33.png  (2159 KB)
global\concept\global_concept_048_c08acd.png  (2361 KB)
global\concept\global_concept_049_22420f.png  (1994 KB)
global\concept\global_concept_050_c6601d.png  (2149 KB)
global\concept\global_concept_084_4d081e.png  (756 KB)
global\concept\global_concept_085_8c237f.png  (589 KB)
global\concept\global_concept_086_eed56d.png  (642 KB)
global\concept\global_concept_087_6845de.png  (671 KB)
global\concept\global_concept_088_fb2871.png  (716 KB)
global\concept\global_concept_089_51c3ee.png  (646 KB)
global\concept\global_concept_090_07345a.png  (694 KB)
global\concept\global_concept_091_3fb76b.png  (751 KB)
global\concept\global_concept_prop_005_c8b1e4.png  (808 KB)
global\storyboard\global_storyboard_029_66b6ab.png  (2226 KB)
global\storyboard\global_storyboard_030_16f31a.png  (2190 KB)
global\test\global_test_001_fe9af3.mp4  (483 KB)
global\video\global_video_001_8925ad.m4a  (143 KB)
global\video\global_video_009_fdc779.wav  (292 KB)
global\video\global_video_010_21e736.mp4  (903 KB)
```

## 三、说明与风险

- 以上文件**不在任何 asset 的 urls 引用中**，删除不影响历史 batch / QC / 作品引用。
- `generated/` 是后端持久化目录，QC 兜底逻辑从这里取图/视频；
  但仅当该文件被 asset 引用时才会被 QC 需要，孤儿文件不会。
- 已归档的成片（export/video）若被 asset 引用则属于「被采纳」，不在清单内。
- 删除命令示例（如需执行，请自行确认后手动执行）：

```powershell
# 按清单删除 36 个孤儿文件（约 57.3 MB）
cmd /c "for /F %%F in (cleanup_list.txt) do del /q D:\1\2\director\backend\data\generated\%%F"
```
