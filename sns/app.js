/**
 * MiniSNS - アプリケーションロジック
 * 技術: Pure JavaScript + localStorage
 * 機能: ユーザー管理、投稿、いいね、コメント、フィード
 */

// ---------------------------------------------------------------------------
// ストレージ管理
// ---------------------------------------------------------------------------
const Storage = {
    KEYS: {
        USERS: 'minisns_users',
        POSTS: 'minisns_posts',
        CURRENT_USER: 'minisns_current_user',
    },

    /** データを取得 */
    get(key) {
        try {
            const data = localStorage.getItem(key);
            return data ? JSON.parse(data) : null;
        } catch {
            return null;
        }
    },

    /** データを保存 */
    set(key, value) {
        localStorage.setItem(key, JSON.stringify(value));
    },

    /** ユーザー一覧を取得 */
    getUsers() {
        return this.get(this.KEYS.USERS) || {};
    },

    /** ユーザーを保存 */
    saveUser(user) {
        const users = this.getUsers();
        users[user.name] = user;
        this.set(this.KEYS.USERS, users);
    },

    /** 投稿一覧を取得（新しい順） */
    getPosts() {
        const posts = this.get(this.KEYS.POSTS) || [];
        return posts.sort((a, b) => b.createdAt - a.createdAt);
    },

    /** 投稿を保存 */
    savePosts(posts) {
        this.set(this.KEYS.POSTS, posts);
    },

    /** 現在のユーザーを取得 */
    getCurrentUser() {
        return this.get(this.KEYS.CURRENT_USER);
    },

    /** 現在のユーザーを設定 */
    setCurrentUser(user) {
        this.set(this.KEYS.CURRENT_USER, user);
    },

    /** ログアウト */
    clearCurrentUser() {
        localStorage.removeItem(this.KEYS.CURRENT_USER);
    },
};


// ---------------------------------------------------------------------------
// ユニークID生成
// ---------------------------------------------------------------------------
function generateId() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}


// ---------------------------------------------------------------------------
// 時刻フォーマット
// ---------------------------------------------------------------------------
function formatTime(timestamp) {
    const now = Date.now();
    const diff = now - timestamp;
    const seconds = Math.floor(diff / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);

    if (seconds < 60) return 'たった今';
    if (minutes < 60) return `${minutes}分前`;
    if (hours < 24) return `${hours}時間前`;
    if (days < 7) return `${days}日前`;

    const date = new Date(timestamp);
    return `${date.getMonth() + 1}/${date.getDate()}`;
}


// ---------------------------------------------------------------------------
// トースト通知
// ---------------------------------------------------------------------------
function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'toastOut 0.3s ease forwards';
        setTimeout(() => toast.remove(), 300);
    }, 2500);
}


// ---------------------------------------------------------------------------
// アプリケーション状態
// ---------------------------------------------------------------------------
const App = {
    currentUser: null,

    /** 初期化 */
    init() {
        this.currentUser = Storage.getCurrentUser();
        this.bindEvents();

        if (this.currentUser) {
            this.showMainScreen();
        } else {
            this.showAuthScreen();
        }
    },

    /** イベントバインド */
    bindEvents() {
        // 認証フォーム
        document.getElementById('auth-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleAuth();
        });

        // ユーザー名入力でバイオ欄を表示
        document.getElementById('username').addEventListener('input', (e) => {
            const name = e.target.value.trim();
            const users = Storage.getUsers();
            const bioGroup = document.getElementById('bio-group');
            const authNote = document.getElementById('auth-note');
            const authBtn = document.getElementById('auth-btn');

            if (name && !users[name]) {
                // 新規ユーザー
                bioGroup.style.display = 'block';
                authBtn.textContent = 'アカウント作成';
                authNote.textContent = '新しいアカウントを作成します';
            } else {
                // 既存ユーザー
                bioGroup.style.display = 'none';
                authBtn.textContent = 'ログイン';
                authNote.textContent = '既にアカウントがあれば同じ名前でログインできます';
            }
        });

        // 投稿テキスト入力
        const postInput = document.getElementById('post-input');
        const charCount = document.getElementById('char-count');
        const postBtn = document.getElementById('post-btn');

        postInput.addEventListener('input', () => {
            const len = postInput.value.length;
            charCount.textContent = len;
            postBtn.disabled = len === 0;

            // 文字数が多い時に色を変える
            if (len > 450) {
                charCount.style.color = 'var(--like-color)';
            } else if (len > 400) {
                charCount.style.color = 'var(--warning-color)';
            } else {
                charCount.style.color = '';
            }
        });

        // 投稿ボタン
        postBtn.addEventListener('click', () => this.createPost());

        // Ctrl+Enter で投稿
        postInput.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && postInput.value.trim()) {
                this.createPost();
            }
        });

        // ログアウト
        document.getElementById('logout-btn').addEventListener('click', () => {
            this.logout();
        });
    },

    // --- 認証 ---
    handleAuth() {
        const nameInput = document.getElementById('username');
        const bioInput = document.getElementById('user-bio');
        const name = nameInput.value.trim();

        if (!name) return;

        const users = Storage.getUsers();
        let user;

        if (users[name]) {
            // 既存ユーザーでログイン
            user = users[name];
            showToast(`おかえり、${name}さん！`, 'success');
        } else {
            // 新規登録
            user = {
                name,
                bio: bioInput.value.trim() || '',
                createdAt: Date.now(),
                avatar: name.charAt(0).toUpperCase(),
            };
            Storage.saveUser(user);
            showToast(`ようこそ、${name}さん！`, 'success');
        }

        this.currentUser = user;
        Storage.setCurrentUser(user);
        this.showMainScreen();
    },

    logout() {
        Storage.clearCurrentUser();
        this.currentUser = null;
        this.showAuthScreen();
        showToast('ログアウトしました');
    },

    // --- 画面切替 ---
    showAuthScreen() {
        document.getElementById('auth-screen').classList.add('active');
        document.getElementById('main-screen').classList.remove('active');
        document.getElementById('username').value = '';
        document.getElementById('user-bio').value = '';
        document.getElementById('bio-group').style.display = 'none';
    },

    showMainScreen() {
        document.getElementById('auth-screen').classList.remove('active');
        document.getElementById('main-screen').classList.add('active');

        // ユーザー情報を表示
        document.getElementById('current-user-name').textContent = this.currentUser.name;
        document.getElementById('composer-avatar').textContent = this.currentUser.avatar;

        this.renderFeed();
    },

    // --- 投稿 ---
    createPost() {
        const input = document.getElementById('post-input');
        const content = input.value.trim();

        if (!content) return;

        const post = {
            id: generateId(),
            author: this.currentUser.name,
            avatar: this.currentUser.avatar,
            content,
            createdAt: Date.now(),
            likes: [],
            comments: [],
        };

        const posts = Storage.getPosts();
        posts.unshift(post);
        Storage.savePosts(posts);

        input.value = '';
        document.getElementById('char-count').textContent = '0';
        document.getElementById('post-btn').disabled = true;

        this.renderFeed();
        showToast('投稿しました！ ✨');
    },

    /** いいね切替 */
    toggleLike(postId) {
        const posts = Storage.getPosts();
        const post = posts.find(p => p.id === postId);
        if (!post) return;

        const userName = this.currentUser.name;
        const idx = post.likes.indexOf(userName);

        if (idx === -1) {
            post.likes.push(userName);
        } else {
            post.likes.splice(idx, 1);
        }

        Storage.savePosts(posts);
        this.renderFeed();
    },

    /** コメント追加 */
    addComment(postId, text) {
        if (!text.trim()) return;

        const posts = Storage.getPosts();
        const post = posts.find(p => p.id === postId);
        if (!post) return;

        post.comments.push({
            id: generateId(),
            author: this.currentUser.name,
            avatar: this.currentUser.avatar,
            text: text.trim(),
            createdAt: Date.now(),
        });

        Storage.savePosts(posts);
        this.renderFeed();
        showToast('コメントしました 💬');
    },

    /** 投稿削除 */
    deletePost(postId) {
        const posts = Storage.getPosts().filter(p => p.id !== postId);
        Storage.savePosts(posts);
        this.renderFeed();
        showToast('投稿を削除しました');
    },

    // --- フィード描画 ---
    renderFeed() {
        const feed = document.getElementById('feed');
        const posts = Storage.getPosts();
        const empty = document.getElementById('feed-empty');

        // 空状態
        if (posts.length === 0) {
            feed.innerHTML = '';
            feed.appendChild(empty);
            empty.style.display = 'block';
            return;
        }

        empty.style.display = 'none';

        // 投稿カードを生成
        const fragment = document.createDocumentFragment();

        for (const post of posts) {
            const card = document.createElement('div');
            card.className = 'post-card';
            card.id = `post-${post.id}`;

            const isLiked = post.likes.includes(this.currentUser.name);
            const isOwner = post.author === this.currentUser.name;

            card.innerHTML = `
                <div class="post-header">
                    <div class="post-avatar">${this.escapeHtml(post.avatar)}</div>
                    <div class="post-meta">
                        <div class="post-author">${this.escapeHtml(post.author)}</div>
                        <div class="post-time">${formatTime(post.createdAt)}</div>
                    </div>
                    ${isOwner ? `<button class="btn-icon post-delete" onclick="App.deletePost('${post.id}')" title="削除">🗑️</button>` : ''}
                </div>
                <div class="post-content">${this.escapeHtml(post.content)}</div>
                <div class="post-actions">
                    <button class="btn-icon ${isLiked ? 'liked' : ''}" onclick="App.toggleLike('${post.id}')">
                        ${isLiked ? '❤️' : '🤍'} <span>${post.likes.length || ''}</span>
                    </button>
                    <button class="btn-icon" onclick="App.toggleComments('${post.id}')">
                        💬 <span>${post.comments.length || ''}</span>
                    </button>
                </div>
                <div class="comments-section" id="comments-${post.id}" style="display:none;">
                    ${post.comments.map(c => `
                        <div class="comment">
                            <div class="comment-avatar">${this.escapeHtml(c.avatar)}</div>
                            <div class="comment-body">
                                <div class="comment-author">${this.escapeHtml(c.author)}</div>
                                <div class="comment-text">${this.escapeHtml(c.text)}</div>
                                <div class="comment-time">${formatTime(c.createdAt)}</div>
                            </div>
                        </div>
                    `).join('')}
                    <div class="comment-form">
                        <input type="text" placeholder="コメントを書く..."
                               maxlength="200"
                               onkeydown="if(event.key==='Enter'){App.addComment('${post.id}',this.value);this.value='';}"
                        >
                        <button class="btn btn-primary btn-sm"
                                onclick="const inp=this.previousElementSibling;App.addComment('${post.id}',inp.value);inp.value='';">
                            送信
                        </button>
                    </div>
                </div>
            `;

            fragment.appendChild(card);
        }

        // DOM更新
        feed.innerHTML = '';
        feed.appendChild(fragment);
    },

    /** コメント欄の表示切替 */
    toggleComments(postId) {
        const section = document.getElementById(`comments-${postId}`);
        if (section) {
            const isHidden = section.style.display === 'none';
            section.style.display = isHidden ? 'block' : 'none';
            if (isHidden) {
                // フォーカスを入力欄に
                const input = section.querySelector('input');
                if (input) input.focus();
            }
        }
    },

    /** XSS対策 */
    escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },
};


// ---------------------------------------------------------------------------
// アプリ起動
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
    App.init();
});
