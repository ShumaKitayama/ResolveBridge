# ResolveBridge

Claude から **DaVinci Resolve（無料版 21.1 で動作確認）** を操作するための、ブリッジと MCP サーバー。

Resolve 21.1 無料版は外部スクリプト（Python / 公式 MCP）を受け付けません。そこで、Resolve 内で動く Lua スクリプトを中継役にして、
Fusion のコンポジションと Resolve API（グローバル変数 `resolve`）を外から扱えるようにしています。

## インストール

必要なもの：macOS、DaVinci Resolve、Python 3.10 以上、（任意）Claude Code の `claude` コマンド、（任意）Xcode Command Line Tools

```bash
git clone <このリポジトリの URL> ResolveBridge
cd ResolveBridge
./install.sh
```

`install.sh` がやること（何度実行しても大丈夫。`git pull` のあとにも実行する）：

1. `.venv/` を作り、`requirements.txt` のパッケージを入れる
2. `runtime/` と認証トークン（`runtime/token.lua`）を作る
3. `utils/*.swift` を `bin/` にビルドする（`swiftc` がなければ飛ばす）
4. `claude_fusion_bridge.lua` を Resolve の `Fusion/Scripts/Utility/` にコピーする。このときクローンした場所のパスを書き込む
5. Claude Code に MCP サーバー `fusion-bridge` を登録する（`--no-mcp` で飛ばせる）

クローンする場所はどこでもよい。フォルダを移動したら `./install.sh` を実行し直す。

## 構成

```
ResolveBridge/
├─ install.sh                         セットアップ
├─ requirements.txt                   Python パッケージ（mcp 1.x, numpy, Pillow, imageio-ffmpeg）
├─ resolve/claude_fusion_bridge.lua   Resolve 内で常駐する中継スクリプト（Lua）
├─ mcp/fusion_bridge_mcp.py           MCP サーバー（Claude Code に fusion-bridge として登録）
├─ mcp/bridge_client.py               CLI 兼ライブラリ（status / exec / file / stop / token）
├─ utils/                             render_frames / export_setting / contact_sheet / verify_frames ほか
│
│  ── 以下は git に入らない（.gitignore）──
├─ .venv/                             Python 環境（install.sh が作る）
├─ bin/                               utils/*.swift のビルド結果
├─ runtime/                           受け渡し用（inbox / outbox / token）— 中身は消してよい
└─ projects/                          ブリッジを使った個人のプロジェクト
```

## 使い方

1. Resolve を起動してプロジェクトを開き、Fusion ページで **ワークスペース › スクリプト › claude_fusion_bridge** をクリック
   （Resolve を起動するたびに 1 回。コンソールに `CLAUDE_BR_START` と出れば起動している）
2. Claude Code からは MCP `fusion-bridge` のツールを使う

   | 種類 | ツール |
   |---|---|
   | 状態 | `bridge_status`, `resolve_info`, `comp_info`, `tool_info`, `list_fonts` |
   | ノード | `add_tool`, `delete_tool`, `connect_nodes`, `add_text`, `add_mask` |
   | 値 | `set_parameter`（数値・文字・真偽値・`[x, y]`、フレーム指定可）, `set_expression`, `animate_parameter` |
   | 出力 | `render_tool_frame`（任意のノードを 1 フレーム PNG に書き出し、画像を返す） |
   | Resolve | `open_page` |
   | 万能 | `fusion_lua`, `fusion_lua_file`（任意の Lua。`comp` / `fu` / `resolve` / `bmd` / `S`） |

   - 書き換え系のツールは Fusion の取り消し 1 回分にまとめている（Resolve で Cmd+Z で戻せる）
   - 引数はすべてエスケープした Lua の値として埋め込む（コードとして実行されない）
   - ツールには MCP の annotations（読み取り専用／破壊的）を付けている
3. CLI から使う場合：
   ```bash
   .venv/bin/python mcp/bridge_client.py status
   ```

### 認証

`runtime/token.lua`（権限 600、`runtime/` は 700）に共有トークンを置いている。最初の実行時に `bridge_client.py` が自動で作る（`bridge_client.py token` でも作れる）。
ブリッジは起動時にこのファイルを読み込み、**ファイル名にトークンが入った命令ファイルだけを実行する**。起動時のログに `auth=on` と出れば有効。
トークンを作り直したら、Resolve でブリッジを起動し直す。エラーメッセージやツールの結果に含まれるトークンは `<token>` に置き換えてある。

受け渡し先は環境変数 `RESOLVE_BRIDGE_HOME` で変えられる（既定はクローンしたフォルダ）。
Dock から起動した Resolve には環境変数が渡らないので、変える場合は `launchctl setenv` を使う。

## 仕組み

- 入力：Claude が `runtime/inbox/cmd_<session>_<n>_<token>.lua` を書く → ブリッジが `fileexists` と `loadfile` で実行する（認証オフのときは `_<token>` なし）
- 出力：結果の JSON を base64 にして `printerr` で `davinci_resolve.log` に書く（2MB でローテーションするので `.log.1` も読む）

## 無料版 21.1 で実測した制約

- メニューから起動した Lua では、`io`、`os.execute`、`ffi`、`bmd.writefile`、UIManager、`Resolve()` が使えない。`comp`、グローバル変数の `resolve`、`fileexists`、`loadfile`、`wait`、`printerr` は使える
- **現在表示中でないコンポに `DoAction("AddSetting")` を実行すると Resolve が落ちる**
- デリバー（レンダー設定）の設定系 API は効かない。ReplaceClip、AppendToTimeline、AddTrack、DeleteClips、ImportMedia は動く
- Custom Tool の式：三角関数は度数法、`pow()` は 0 を返す（`^` を使う）、`exp()` は不安定。式はインタプリタで評価されるので重い
- `UserControls` は書き込めるが読み取れない。`ordered()` も使えない → コントローラーは .setting ファイルで定義して `AddSetting` で読み込む
- Resolve 内の Fusion Loader では .mov を読めない（PNG 連番は読める）
- パーティクルの速度は 1 秒あたりの値
- Background の色はアルファでプリマルチプライされない
- 既定位置（-32768）で `AddTool` すると、選択中のノードに自動でつながる／Merge が自動で作られる → ツールは必ず座標を指定して追加している
- Text+ はフォントとスタイルの組み合わせが実在しないと描画に失敗する（例：Hiragino Sans に "Bold" はなく W0〜W9）。`fu.FontManager:GetFontList()` で確認できる
- **Saver の出力形式を QuickTime（.mov）にして書き出すと Resolve が落ちる**（21.1 無料版で実測）。動画は PNG 連番 → ffmpeg で作る
- BezierSpline／Path をつなぐと、その時点の再生位置に自動でキーフレームが 1 つ打たれる → 再生位置を最初のキーの時刻に移してからつなぐ
- タイムラインを開き直すと、Fusion クリップのコンポの範囲がクリップの長さに戻る → 範囲を指定した `Render()` の前に `COMPN_GlobalEnd` を設定し直す
- 設定を変えたプロジェクトでは `CreateEmptyTimeline` が nil を返すことがある → 既存のタイムラインを `DuplicateTimeline` で複製する
- `comp:Render({Start, End})` はコンポのレンダー範囲を書き換える → `render_tool_frame` は書き出し後に元の範囲に戻している
