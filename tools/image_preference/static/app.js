/**
 * 画像好み判定ツール - フロントエンドロジック
 */

// ========== 初期化 ==========
document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    initDropZones();
    initFileInputs();
    updateStatus();
    loadTrainingImages();
});

// ========== タブ制御 ==========
function initTabs() {
    document.querySelectorAll(".tab").forEach((tab) => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
            document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
            tab.classList.add("active");
            document.getElementById(`panel-${tab.dataset.tab}`).classList.add("active");
        });
    });
}

// ========== ステータス更新 ==========
async function updateStatus() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();
        const badge = document.getElementById("statusBadge");
        const dot = badge.querySelector(".status-dot");
        const text = badge.querySelector(".status-text");

        if (data.model_ready) {
            dot.className = "status-dot ready";
            text.textContent = `学習済み（${data.total_images}枚）`;
        } else {
            dot.className = "status-dot no-model";
            text.textContent = `未学習（${data.total_images}枚）`;
        }

        // カテゴリ別画像数を更新
        for (const [label, count] of Object.entries(data.image_counts)) {
            const el = document.getElementById(`count-${label}`);
            if (el) el.textContent = `${count}枚`;
        }
    } catch (e) {
        console.error("ステータス取得エラー:", e);
    }
}

// ========== ドラッグ＆ドロップ ==========
function initDropZones() {
    // 判定用ドロップゾーン
    const judgeZone = document.getElementById("judgeDropZone");
    setupDropZone(judgeZone, (files) => {
        if (files.length > 0) judgeImage(files[0]);
    });

    // トレーニング用ドロップゾーン
    document.querySelectorAll(".category-dropzone").forEach((zone) => {
        setupDropZone(zone, (files) => {
            const category = zone.dataset.category;
            uploadImages(category, files);
        });
    });
}

function setupDropZone(zone, onDrop) {
    zone.addEventListener("dragover", (e) => {
        e.preventDefault();
        zone.classList.add("drag-over");
    });

    zone.addEventListener("dragleave", () => {
        zone.classList.remove("drag-over");
    });

    zone.addEventListener("drop", (e) => {
        e.preventDefault();
        zone.classList.remove("drag-over");
        const files = Array.from(e.dataTransfer.files).filter((f) =>
            f.type.startsWith("image/")
        );
        if (files.length > 0) onDrop(files);
    });

    zone.addEventListener("click", (e) => {
        if (e.target.tagName !== "INPUT" && !e.target.closest("label")) {
            const input = zone.querySelector('input[type="file"]');
            if (input) input.click();
        }
    });
}

// ========== ファイル入力 ==========
function initFileInputs() {
    // 判定用
    document.getElementById("judgeFileInput").addEventListener("change", (e) => {
        if (e.target.files.length > 0) judgeImage(e.target.files[0]);
    });

    // トレーニング用
    document.querySelectorAll(".category-file-input").forEach((input) => {
        input.addEventListener("change", (e) => {
            const category = input.dataset.category;
            uploadImages(category, Array.from(e.target.files));
        });
    });
}

// ========== 画像判定 ==========
async function judgeImage(file) {
    // プレビュー表示
    const preview = document.getElementById("judgePreview");
    const previewImg = document.getElementById("previewImage");
    const reader = new FileReader();
    reader.onload = (e) => {
        previewImg.src = e.target.result;
        preview.classList.remove("hidden");
    };
    reader.readAsDataURL(file);

    // 判定リクエスト
    const resultCard = document.getElementById("judgeResult");
    resultCard.classList.add("hidden");

    const formData = new FormData();
    formData.append("image", file);

    try {
        const res = await fetch("/api/judge", { method: "POST", body: formData });
        const data = await res.json();

        if (!data.success) {
            alert(data.error || "判定エラーが発生しました");
            return;
        }

        const result = data.result;
        showJudgeResult(result);
    } catch (e) {
        alert("判定エラー: " + e.message);
    }
}

function showJudgeResult(result) {
    const resultCard = document.getElementById("judgeResult");
    const circle = document.getElementById("verdictCircle");
    const verdictText = document.getElementById("verdictText");
    const verdictLabel = document.getElementById("verdictLabel");
    const scoreFill = document.getElementById("scoreFill");
    const scoreValue = document.getElementById("scoreValue");

    // OK/NO判定表示
    circle.className = `verdict-circle ${result.verdict.toLowerCase()}`;
    verdictText.textContent = result.verdict;
    verdictLabel.textContent = result.label;

    // 好き度スコア
    const score = result.like_score;
    scoreFill.style.width = `${score}%`;
    scoreFill.style.background =
        score >= 60
            ? "linear-gradient(90deg, #06d6a0, #0cce6b)"
            : score >= 30
                ? "linear-gradient(90deg, #ffd166, #f4a261)"
                : "linear-gradient(90deg, #ef476f, #d62828)";
    scoreValue.textContent = Math.round(score);

    // 確率分布
    const probDiv = document.getElementById("probabilities");
    probDiv.innerHTML = "";
    const probColors = {
        "好き": "var(--accent-like)",
        "そうでもない": "var(--accent-neutral)",
        "嫌い": "var(--accent-dislike)",
    };
    for (const [label, prob] of Object.entries(result.probabilities)) {
        probDiv.innerHTML += `
            <div class="prob-item">
                <div class="prob-label">${label}</div>
                <div class="prob-value" style="color: ${probColors[label] || '#fff'}">${(prob * 100).toFixed(1)}%</div>
            </div>
        `;
    }

    resultCard.classList.remove("hidden");
}

// ========== 画像アップロード ==========
async function uploadImages(category, files) {
    const formData = new FormData();
    formData.append("category", category);
    for (const file of files) {
        formData.append("images", file);
    }

    try {
        const res = await fetch("/api/upload", { method: "POST", body: formData });
        const data = await res.json();
        if (data.success) {
            updateStatus();
            loadTrainingImages();
        } else {
            alert(data.error || "アップロードエラー");
        }
    } catch (e) {
        alert("アップロードエラー: " + e.message);
    }
}

// ========== トレーニング画像一覧 ==========
async function loadTrainingImages() {
    try {
        const res = await fetch("/api/training-images");
        const data = await res.json();

        for (const [label, images] of Object.entries(data)) {
            const container = document.getElementById(`images-${label}`);
            if (!container) continue;
            container.innerHTML = "";
            for (const img of images) {
                const wrapper = document.createElement("div");
                wrapper.className = "image-wrapper";
                wrapper.style.position = "relative";
                wrapper.style.cursor = "pointer";

                const imgEl = document.createElement("img");
                imgEl.src = `/${img.path}`;
                imgEl.alt = img.name;
                imgEl.title = img.reason ? `📝 ${img.reason}` : img.name;
                imgEl.loading = "lazy";

                // 理由バッジ
                if (img.reason) {
                    const badge = document.createElement("span");
                    badge.className = "reason-badge";
                    badge.textContent = "📝";
                    badge.title = img.reason;
                    wrapper.appendChild(badge);
                }

                // クリックでモーダルを開く
                wrapper.addEventListener("click", () => {
                    openReasonModal(label, img.name, `/${img.path}`, img.reason || "");
                });

                wrapper.appendChild(imgEl);
                container.appendChild(wrapper);
            }
        }
    } catch (e) {
        console.error("トレーニング画像取得エラー:", e);
    }
}

// ========== 学習 ==========
async function startTraining() {
    const btn = document.getElementById("trainButton");
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span> 学習中...';

    try {
        const res = await fetch("/api/train", { method: "POST" });
        const data = await res.json();

        if (!data.success) {
            alert(data.error || "学習エラー");
            return;
        }

        const stats = data.stats;
        const resultDiv = document.getElementById("trainingResult");
        const statsDiv = document.getElementById("trainingStats");

        statsDiv.innerHTML = `
            <div class="stat-row">
                <span class="stat-label">総画像数</span>
                <span class="stat-value">${stats.total_images}枚</span>
            </div>
            ${Object.entries(stats.per_label)
                .map(
                    ([name, count]) => `
                <div class="stat-row">
                    <span class="stat-label">${name}</span>
                    <span class="stat-value">${count}枚</span>
                </div>
            `
                )
                .join("")}
            ${stats.cv_accuracy
                ? `<div class="stat-row">
                    <span class="stat-label">CV精度</span>
                    <span class="stat-value">${(stats.cv_accuracy * 100).toFixed(1)}%</span>
                </div>`
                : ""
            }
            <div style="margin-top: 16px;">
                <h4 style="font-size: 14px; margin-bottom: 8px; color: var(--accent-primary);">🔑 重要な特徴量 TOP 5</h4>
                ${stats.top_features
                .slice(0, 5)
                .map(
                    ([name, imp]) => `
                    <div class="stat-row">
                        <span class="stat-label">${name}</span>
                        <span class="stat-value">${(imp * 100).toFixed(1)}%</span>
                    </div>
                `
                )
                .join("")}
            </div>
        `;
        resultDiv.classList.remove("hidden");
        updateStatus();
    } catch (e) {
        alert("学習エラー: " + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span class="btn-icon">🚀</span> 学習を開始する';
    }
}

// ========== 分析 ==========
async function runAnalysis() {
    const resultDiv = document.getElementById("analysisResult");
    resultDiv.classList.add("hidden");

    try {
        const res = await fetch("/api/analyze");
        const data = await res.json();

        if (!data.success) {
            alert("分析エラー");
            return;
        }

        const analysis = data.analysis;

        // 好みプロファイル
        const profileDiv = document.getElementById("preferenceProfile");
        const profileContent = document.getElementById("profileContent");
        if (analysis.preference_profile) {
            const profile = analysis.preference_profile;
            let profileHtml = "";

            // 好きな要素タグ
            if (profile.likes && profile.likes.length > 0) {
                profileHtml += '<div class="profile-tags">';
                profileHtml += '<span class="profile-tags-label">❤️ 好き:</span>';
                for (const p of profile.likes) {
                    const confClass = p.confidence === "高" ? "high" : "medium";
                    const sourceText = Array.isArray(p.source) ? p.source.join("+") : p.source;
                    profileHtml += `<span class="pref-tag like ${confClass}" title="ソース: ${sourceText} / 信頼度: ${p.confidence}">${p.label}</span>`;
                }
                profileHtml += '</div>';
            }

            // 嫌いな要素タグ
            if (profile.dislikes && profile.dislikes.length > 0) {
                profileHtml += '<div class="profile-tags">';
                profileHtml += '<span class="profile-tags-label">💔 苦手:</span>';
                for (const p of profile.dislikes) {
                    const confClass = p.confidence === "高" ? "high" : "medium";
                    const sourceText = Array.isArray(p.source) ? p.source.join("+") : p.source;
                    profileHtml += `<span class="pref-tag dislike ${confClass}" title="ソース: ${sourceText} / 信頼度: ${p.confidence}">${p.label}</span>`;
                }
                profileHtml += '</div>';
            }

            // プロファイルテキスト
            if (profile.profile_text) {
                profileHtml += `<div class="profile-text">${profile.profile_text.replace(/\n/g, '<br>')}</div>`;
            }

            profileContent.innerHTML = profileHtml;
            profileDiv.classList.remove("hidden");
        } else {
            profileDiv.classList.add("hidden");
        }

        // サマリー
        const summaryDiv = document.getElementById("analysisSummary");
        summaryDiv.innerHTML = `
            <h3>💡 あなたの好みの傾向</h3>
            ${(analysis.summary || [])
                .map((s) => `<div class="summary-item">${s}</div>`)
                .join("")}
        `;

        // 詳細
        const detailsDiv = document.getElementById("analysisDetails");
        detailsDiv.innerHTML = "";

        // 色彩分析
        if (analysis.color_preference) {
            detailsDiv.innerHTML += buildDetailCard("🎨 色彩傾向", analysis.color_preference);
        }

        // 明るさ分析
        if (analysis.brightness_preference) {
            detailsDiv.innerHTML += buildDetailCard("☀️ 明るさ傾向", analysis.brightness_preference);
        }

        // 構図分析
        if (analysis.composition_preference) {
            detailsDiv.innerHTML += buildDetailCard("📐 構図傾向", analysis.composition_preference);
        }

        // テクスチャ分析
        if (analysis.texture_preference) {
            detailsDiv.innerHTML += buildDetailCard("🔍 テクスチャ傾向", analysis.texture_preference);
        }

        // 特徴量重要度
        if (analysis.feature_importance) {
            let html = '<div class="detail-card" style="grid-column: 1 / -1;"><h4>🔑 特徴量重要度 TOP 10</h4>';
            for (const [name, imp] of analysis.feature_importance.slice(0, 10)) {
                const barWidth = (imp * 500).toFixed(0);
                html += `
                    <div class="detail-row" style="align-items: center;">
                        <span>${name}</span>
                        <div style="flex:1; margin: 0 12px;">
                            <div style="width: ${barWidth}px; max-width: 100%; height: 6px; background: linear-gradient(90deg, var(--accent-primary), #c084fc); border-radius: 3px;"></div>
                        </div>
                        <span class="val">${(imp * 100).toFixed(1)}%</span>
                    </div>
                `;
            }
            html += "</div>";
            detailsDiv.innerHTML += html;
        }

        resultDiv.classList.remove("hidden");
    } catch (e) {
        alert("分析エラー: " + e.message);
    }
}

function buildDetailCard(title, data) {
    let html = `<div class="detail-card"><h4>${title}</h4>`;
    for (const [category, values] of Object.entries(data)) {
        html += `<div style="margin-bottom: 8px; font-size: 12px; color: var(--accent-primary); font-weight: 600;">${category}</div>`;
        for (const [key, val] of Object.entries(values)) {
            html += `<div class="detail-row"><span>${key}</span><span class="val">${typeof val === "number" ? val.toFixed(3) : val}</span></div>`;
        }
    }
    html += "</div>";
    return html;
}

// ========== 理由モーダル ==========
let currentModalData = { category: "", filename: "" };

function openReasonModal(category, filename, imgUrl, existingReason) {
    currentModalData = { category, filename };

    const modal = document.getElementById("reasonModal");
    const modalImage = document.getElementById("modalImage");
    const modalTitle = document.getElementById("modalTitle");
    const modalCategory = document.getElementById("modalCategory");
    const modalInput = document.getElementById("modalReasonInput");
    const refinePreview = document.getElementById("refinePreview");

    modalImage.src = imgUrl;
    modalTitle.textContent = filename;

    const categoryEmoji = { "好き": "❤️", "そうでもない": "😐", "嫌い": "💔" };
    modalCategory.textContent = `${categoryEmoji[category] || ""} ${category}`;
    modalCategory.className = `modal-category ${category === "好き" ? "like" : category === "嫌い" ? "dislike" : "neutral"}`;

    // 校正プレビューをリセット
    refinePreview.classList.add("hidden");

    if (existingReason) {
        // 既存の理由がある場合はそのまま表示
        modalInput.value = existingReason;
        modalInput.placeholder = "理由を編集できます...";
    } else {
        // AIが画像を分析して理由を自動提案する
        modalInput.value = "";
        modalInput.placeholder = "🤖 AIが画像を分析中...";
        modalInput.disabled = true;
        fetchImageDescription(category, filename, modalInput);
    }

    modal.classList.remove("hidden");
}

async function fetchImageDescription(category, filename, inputEl) {
    try {
        const res = await fetch("/api/describe-image", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ category, filename }),
        });
        const data = await res.json();
        if (data.success && data.description) {
            inputEl.value = data.description;
            inputEl.placeholder = "AIの提案です。自由に編集してから保存してください";
        } else {
            inputEl.placeholder = "理由を自由に入力してください...";
        }
    } catch (e) {
        inputEl.placeholder = "理由を自由に入力してください...";
    } finally {
        inputEl.disabled = false;
        inputEl.focus();
    }
}

function closeReasonModal() {
    document.getElementById("reasonModal").classList.add("hidden");
}

async function saveReasonFromModal() {
    const reason = document.getElementById("modalReasonInput").value.trim();
    const { category, filename } = currentModalData;

    try {
        const res = await fetch("/api/reason", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ category, filename, reason }),
        });
        const data = await res.json();
        if (data.success) {
            closeReasonModal();
            loadTrainingImages();
        } else {
            alert(data.error || "保存エラー");
        }
    } catch (e) {
        alert("保存エラー: " + e.message);
    }
}

// ESCキーでモーダルを閉じる
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeReasonModal();
});

// ========== テキスト校正 ==========
let lastRefinedText = "";

async function refineReasonText() {
    const input = document.getElementById("modalReasonInput");
    const raw = input.value.trim();
    if (!raw) {
        alert("先にテキストを入力してください");
        return;
    }

    const { category } = currentModalData;
    const btn = document.querySelector(".refine-btn");
    btn.textContent = "⏳ 校正中...";
    btn.disabled = true;

    try {
        const res = await fetch("/api/refine-reason", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: raw, category }),
        });
        const data = await res.json();
        if (data.success) {
            lastRefinedText = data.refined;
            const preview = document.getElementById("refinePreview");
            const textEl = document.getElementById("refineText");
            const kwEl = document.getElementById("refineKeywords");

            textEl.textContent = data.refined;

            if (data.keywords && data.keywords.length > 0) {
                kwEl.innerHTML = data.keywords
                    .map(kw => `<span class="refine-kw-tag">${kw}</span>`)
                    .join("");
            } else {
                kwEl.innerHTML = '<span style="color: var(--text-muted); font-size: 12px;">キーワードが検出されませんでした。もう少し具体的に書くと校正精度が上がります。</span>';
            }

            preview.classList.remove("hidden");
        } else {
            alert(data.error || "校正エラー");
        }
    } catch (e) {
        alert("校正エラー: " + e.message);
    } finally {
        btn.textContent = "✨ AIが校正";
        btn.disabled = false;
    }
}

function applyRefinedText() {
    if (lastRefinedText) {
        document.getElementById("modalReasonInput").value = lastRefinedText;
        document.getElementById("refinePreview").classList.add("hidden");
    }
}
