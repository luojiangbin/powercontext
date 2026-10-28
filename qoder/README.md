# 课程资料速查（StudyIndex）

把课件、笔记、论文摘录丢进 **seekdb**，用关键词一秒查回来，并导出可直接发布的静态站。

## 解决什么问题

复习时想找"红黑树的性质在哪篇笔记里"，翻文件夹要翻半天；课程资料散落在不同目录，靠文件名根本搜不到内容。这个工具把笔记正文存进 seekdb 做全文检索，再把索引导出成静态站，手机上也能查。

## 目录结构

```
studyindex.py      CLI：init / ingest / search / build-site
seekdb_store.py    seekdb 存储与关键字检索
notes/             示例笔记（数据结构、数据库）
site/              生成的静态站（发布用）
```

## 运行

前置：seekdb 已启动（默认 `127.0.0.1:2881`，用户 `root`，空密码）。

```bash
pip install -r requirements.txt

python studyindex.py init            # 建库建表
python studyindex.py ingest notes    # 导入笔记
python studyindex.py search 红黑树    # 命令行检索
python studyindex.py search 索引 --course 数据库   # 只在该课程内检索
python studyindex.py build-site      # 生成 site/
```

环境变量可覆盖连接：`SEEKDB_HOST`、`SEEKDB_PORT`、`SEEKDB_USER`、`SEEKDB_PASSWORD`。

## 发布

把 `site/` 目录用 Qoder Sites 发布，得到公开访问的网址。

- 在线站点：https://studyindex-o1aofm6py70.qoder.website
- 发布目录：`dist/`（Qoder Sites 部署配置见 `.StudyIndex 课程资料速查.qoder.site`）

## 已知限制

- 检索是 `LIKE` 关键字匹配，不是语义检索；同义词需要自己补充关键词。
- 静态站搜索用导出索引做客户端过滤，不连数据库；数据更新后要重新 `build-site`。
