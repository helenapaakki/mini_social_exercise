(() => {
  "use strict";

  const initialPosts = [
    { id: 1, user: "alice", content: "Hello everyone! I'm excited to be part of this community. What are you all working on this week?", reactions: 4, liked: false, comments: ["Welcome, Alice!"], created: 3 },
    { id: 2, user: "bob", content: "I went for a long walk today and found a lovely little café. A good reminder to take a break sometimes.", reactions: 2, liked: false, comments: [], created: 2 },
    { id: 3, user: "charlie", content: "Does anyone have recommendations for beginner-friendly photography projects?", reactions: 7, liked: false, comments: ["Try taking photos of everyday objects!"], created: 1 }
  ];

  const peopleSeed = [
    { username: "alice", name: "Alice Morgan", bio: "Technology enthusiast, reader and coffee fan.", location: "Helsinki", following: false },
    { username: "bob", name: "Bob Taylor", bio: "Enjoys hiking, cooking and discovering new places.", location: "Oulu", following: false },
    { username: "charlie", name: "Charlie Lee", bio: "Learning photography and sharing small creative projects.", location: "Turku", following: false }
  ];

  let posts, people, completed, visitedProfile, activeView, currentSort, currentFilter, selectedProfile;
  const viewOrder = ["feed", "create", "reactions", "people", "profile", "challenge"];
  const viewNames = ["Home feed", "Create a post", "React and comment", "Find and follow people", "Explore a profile", "Final challenge"];
  const $ = (selector) => document.querySelector(selector);
  const escapeHTML = (value) => String(value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const person = (username) => people.find(p => p.username === username);
  const authorName = (username) => username === "practice_user" ? "You" : (person(username)?.name || username);

  function resetState() {
    posts = initialPosts.map(post => ({ ...post, comments: [...post.comments] }));
    people = peopleSeed.map(p => ({ ...p }));
    completed = { create: false, react: false, comment: false, follow: false, profile: false };
    visitedProfile = false;
    activeView = "feed";
    currentSort = "new";
    currentFilter = "all";
  }

  function postCard(post, includeCommentForm = false) {
    const isOwn = post.user === "practice_user";
    const author = authorName(post.user);
    const followMark = !isOwn && person(post.user)?.following ? '<span class="text-success ms-1" title="Following">✓</span>' : "";
    return `
      <article class="card shadow-sm post-card" data-post-id="${post.id}">
        <div class="card-body">
          <div class="d-flex align-items-center gap-2 mb-3">
            <div class="avatar">${escapeHTML(author.slice(0,1).toUpperCase())}</div>
            <div class="flex-grow-1">
              <strong>${escapeHTML(author)}${followMark}</strong>
              <div class="small text-muted">@${escapeHTML(post.user)}</div>
            </div>
            <small class="text-muted">Practice post</small>
          </div>
          <p class="post-content mb-3">${escapeHTML(post.content).replace(/\n/g, "<br>")}</p>
          <div class="d-flex flex-wrap align-items-center gap-2 border-top pt-2">
            <button class="btn btn-sm ${post.liked ? "btn-danger" : "btn-light"} reaction-button" data-action="react" data-id="${post.id}" type="button">${post.liked ? "♥ Reacted" : "♡ React"} · <span class="reaction-count">${post.reactions}</span></button>
            <button class="btn btn-sm btn-light" data-action="toggle-comments" data-id="${post.id}" type="button">💬 Comment (${post.comments.length})</button>
            ${!isOwn ? `<button class="btn btn-sm btn-outline-secondary ms-auto" data-action="profile" data-user="${escapeHTML(post.user)}" type="button">View profile</button>` : ""}
          </div>
          <div class="comment-area mt-3 ${includeCommentForm ? "" : "d-none"}" id="comments-${post.id}">
            <div class="comments-list">${post.comments.map(c => `<div class="small border-top py-2"><strong>You / community</strong><div>${escapeHTML(c)}</div></div>`).join("")}</div>
            <form class="comment-form mt-2" data-post-id="${post.id}">
              <label class="visually-hidden" for="comment-${post.id}">Write a comment</label>
              <div class="input-group input-group-sm">
                <input class="form-control" id="comment-${post.id}" name="comment" maxlength="250" placeholder="Write a comment..." required>
                <button class="btn btn-outline-primary" type="submit">Send</button>
              </div>
            </form>
          </div>
        </div>
      </article>`;
  }

  function renderFeed() {
    let visible = [...posts];
    if (currentFilter === "following") visible = visible.filter(p => person(p.user)?.following);
    if (currentSort === "popular") visible.sort((a,b) => b.reactions - a.reactions);
    else visible.sort((a,b) => b.created - a.created);
    $("#feedPosts").innerHTML = visible.length ? visible.map(p => postCard(p)).join("") : '<div class="alert alert-light border">No posts from followed people yet. Follow someone in the Find and follow people section.</div>';
    document.querySelectorAll(".feed-sort").forEach(b => {
      const active = b.dataset.sort === currentSort;
      b.classList.toggle("btn-primary", active); b.classList.toggle("btn-outline-secondary", !active);
    });
    document.querySelectorAll(".feed-filter").forEach(b => {
      const active = b.dataset.filter === currentFilter;
      b.classList.toggle("btn-primary", active); b.classList.toggle("btn-outline-secondary", !active);
    });
  }

  function renderPeople() {
    $("#peopleList").innerHTML = people.map(p => `
      <div class="col-md-6">
        <div class="card h-100 shadow-sm"><div class="card-body">
          <div class="d-flex align-items-center gap-2 mb-2"><div class="avatar">${escapeHTML(p.name[0])}</div><div><strong>${escapeHTML(p.name)}</strong><div class="small text-muted">@${escapeHTML(p.username)}</div></div></div>
          <p class="small mb-1">${escapeHTML(p.bio)}</p><p class="small text-muted">📍 ${escapeHTML(p.location)}</p>
          <div class="d-flex gap-2">
            <button class="btn btn-sm ${p.following ? "btn-secondary" : "btn-primary"}" data-action="follow" data-user="${p.username}" type="button">${p.following ? "Following · Unfollow" : "Follow"}</button>
            <button class="btn btn-sm btn-outline-secondary" data-action="profile" data-user="${p.username}" type="button">View profile</button>
          </div>
        </div></div>
      </div>`).join("");
  }

  function renderProfile(username = (selectedProfile || "alice")) {
    const p = person(username) || people[0];
    const userPosts = posts.filter(post => post.user === p.username);
    $("#profilePanel").innerHTML = `
      <div class="card shadow-sm mb-3"><div class="card-body">
        <div class="d-flex align-items-center gap-3"><div class="avatar avatar-large">${escapeHTML(p.name[0])}</div><div><h3 class="h5 mb-1">${escapeHTML(p.name)}</h3><div class="text-muted">@${escapeHTML(p.username)}</div></div></div>
        <p class="mt-3 mb-1">${escapeHTML(p.bio)}</p><div class="small text-muted">📍 ${escapeHTML(p.location)}</div>
        <div class="d-flex flex-wrap gap-3 small mt-3"><span><strong>${userPosts.length}</strong> Posts</span><span><strong>${p.following ? 1 : 0}</strong> Following (your practice status)</span></div>
        <div class="mt-3"><button class="btn btn-sm ${p.following ? "btn-secondary" : "btn-primary"}" data-action="follow" data-user="${p.username}" type="button">${p.following ? "Unfollow" : "Follow"}</button></div>
      </div></div>
      <h3 class="h6">Latest posts</h3>
      ${userPosts.length ? userPosts.map(post => postCard(post)).join("") : '<div class="alert alert-light border">This member has no practice posts yet.</div>'}`;
  }

  function renderReactionExercise() {
    const sample = posts.find(p => p.id === 1) || posts[0];
    $("#reactionPracticePost").innerHTML = sample ? postCard(sample, true) : "";
  }

  function updateProgress() {
    const count = Object.values(completed).filter(Boolean).length;
    $("#progressBar").style.width = `${count / 5 * 100}%`;
    $("#progressText").textContent = `${count} of 5 activities completed`;
    document.querySelector('[aria-label="Tutorial progress"]').setAttribute("aria-valuenow", count);
    $("#challengeProgress").style.width = `${count / 5 * 100}%`;
    $("#challengeText").textContent = `${count} of 5 challenge tasks complete.`;
    $("#challengeComplete").classList.toggle("d-none", count !== 5);
    const map = { create: "challengePost", react: "challengeReact", comment: "challengeComment", follow: "challengeFollow", profile: "challengeProfile" };
    Object.entries(map).forEach(([key,id]) => { $("#" + id).checked = completed[key]; });
  }

  function showView(view) {
    if (!viewOrder.includes(view)) return;
    activeView = view;
    document.querySelectorAll(".sandbox-view").forEach(section => section.classList.toggle("d-none", section.id !== `view-${view}`));
    document.querySelectorAll("[data-view]").forEach(button => button.classList.toggle("active", button.dataset.view === view));
    $("#viewLabel").textContent = viewNames[viewOrder.indexOf(view)];
    $("#previousView").classList.toggle("d-none", view === "feed");
    $("#nextView").classList.toggle("d-none", view === "challenge");
    $("#nextView").disabled = viewOrder.indexOf(view) === viewOrder.length - 1;
    if (view === "feed") renderFeed();
    if (view === "people") renderPeople();
    if (view === "profile") renderProfile(selectedProfile);
    if (view === "reactions") renderReactionExercise();
    if (view === "challenge") updateProgress();
  }

  function toggleFollow(username) {
    const p = person(username);
    if (!p) return;
    p.following = !p.following;
    if (p.following) completed.follow = true;
    renderPeople(); renderProfile(username); renderFeed(); updateProgress();
  }

  document.addEventListener("click", (event) => {
    const nav = event.target.closest("[data-view]");
    if (nav) { showView(nav.dataset.view); return; }
    const go = event.target.closest("[data-go]");
    if (go) { showView(go.dataset.go); return; }
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    const id = Number(button.dataset.id);
    const post = posts.find(p => p.id === id);
    if (action === "follow") { toggleFollow(button.dataset.user); return; }
    if (action === "profile") {
      visitedProfile = true; completed.profile = true;
      renderProfile(button.dataset.user); showView("profile"); updateProgress(); return;
    }
    if (!post) return;
    if (action === "react") {
      post.liked = !post.liked; post.reactions += post.liked ? 1 : -1;
      completed.react = true;
      renderFeed(); renderReactionExercise(); renderProfile(); updateProgress();
    }
    if (action === "toggle-comments") {
      const area = document.getElementById(`comments-${id}`);
      if (area) area.classList.toggle("d-none");
    }
  });

  document.addEventListener("submit", (event) => {
    if (event.target.id === "createPostForm") {
      event.preventDefault();
      const field = $("#postContent");
      const content = field.value.trim();
      if (!content) return;
      posts.unshift({ id: Date.now(), user: "practice_user", content, reactions: 0, liked: false, comments: [], created: Date.now() });
      completed.create = true;
      field.value = ""; $("#postCharCount").textContent = "0";
      renderFeed(); updateProgress();
      showView("feed");
      return;
    }
    if (event.target.matches(".comment-form")) {
      event.preventDefault();
      const id = Number(event.target.dataset.postId);
      const post = posts.find(p => p.id === id);
      const input = event.target.elements.comment;
      const comment = input.value.trim();
      if (!post || !comment) return;
      post.comments.push(comment);
      completed.comment = true;
      renderReactionExercise(); renderFeed(); renderProfile(); updateProgress();
    }
  });

  document.addEventListener("click", (event) => {
    const sort = event.target.closest(".feed-sort");
    if (sort) { currentSort = sort.dataset.sort; renderFeed(); }
    const filter = event.target.closest(".feed-filter");
    if (filter) { currentFilter = filter.dataset.filter; renderFeed(); }
  });

  $("#postContent").addEventListener("input", event => { $("#postCharCount").textContent = event.target.value.length; });
  $("#previousView").addEventListener("click", () => showView(viewOrder[Math.max(0, viewOrder.indexOf(activeView) - 1)]));
  $("#nextView").addEventListener("click", () => showView(viewOrder[Math.min(viewOrder.length - 1, viewOrder.indexOf(activeView) + 1)]));
  $("#resetSandbox").addEventListener("click", () => {
    if (!window.confirm("Reset all practice posts, reactions, comments, follows and progress?")) return;
    resetState();
    $("#postContent").value = ""; $("#postCharCount").textContent = "0";
    renderFeed(); renderPeople(); renderProfile(); renderReactionExercise(); updateProgress(); showView("feed");
  });

  resetState();
  renderFeed(); renderPeople(); renderProfile(); renderReactionExercise(); updateProgress(); showView("feed");
})();