from flask import Flask, render_template, request, redirect, url_for, session, flash, g, abort
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
import collections
import json
import sqlite3
import hashlib
import re
import os
from werkzeug.utils import secure_filename
from datetime import datetime

app = Flask(__name__)
app.secret_key = '123456789' 
DATABASE = 'database.sqlite'

app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # Limit upload size to 5MB

ALLOWED_PROFILE_PICTURE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# Load censorship data
# WARNING! The censorship.dat file contains disturbing language when decrypted. 
# If you want to test whether moderation works, 
# you can trigger censorship using these words: 
# tier1badword, tier2badword, tier3badword
ENCRYPTED_FILE_PATH = 'censorship.dat'
fernet = Fernet('xpplx11wZUibz0E8tV8Z9mf-wwggzSrc21uQ17Qq2gg=')
with open(ENCRYPTED_FILE_PATH, 'rb') as encrypted_file:
    encrypted_data = encrypted_file.read()
decrypted_data = fernet.decrypt(encrypted_data)
MODERATION_CONFIG = json.loads(decrypted_data)
TIER1_WORDS = MODERATION_CONFIG['categories']['tier1_severe_violations']['words']
TIER2_PHRASES = MODERATION_CONFIG['categories']['tier2_spam_scams']['phrases']
TIER3_WORDS = MODERATION_CONFIG['categories']['tier3_mild_profanity']['words']

def get_db():
    """
    Connect to the application's configured database. The connection
    is unique for each request and will be reused if this is called
    again.
    """
    if 'db' not in g:
        g.db = sqlite3.connect(
            DATABASE,
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row

        user_columns = {
            row['name']
            for row in g.db.execute(
                'PRAGMA table_info(users)'
            ).fetchall()
        }

        if 'display_name' not in user_columns:
            g.db.execute(
                'ALTER TABLE users ADD COLUMN display_name TEXT'
            )

        if 'profile_picture' not in user_columns:
            g.db.execute(
                'ALTER TABLE users ADD COLUMN profile_picture TEXT'
            )

        g.db.commit()

        g.db.execute('''
            CREATE TABLE IF NOT EXISTS tag_blocks (
                blocker_id INTEGER NOT NULL,
                blocked_user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (blocker_id, blocked_user_id),
                CHECK (blocker_id != blocked_user_id)
            )
        ''')
        g.db.commit()

    return g.db


@app.teardown_appcontext
def close_connection(exception):
    """Closes the database again at the end of the request."""
    db = g.pop('db', None)

    if db is not None:
        db.close()


def query_db(query, args=(), one=False, commit=False):
    """
    Queries the database and returns a list of dictionaries, a single
    dictionary, or None. Also handles write operations.
    """
    db = get_db()
    
    # Using 'with' on a connection object implicitly handles transactions.
    # The 'with' statement will automatically commit if successful, 
    # or rollback if an exception occurs. This is safer.
    try:
        with db:
            cur = db.execute(query, args)
        
        # For SELECT statements, fetch the results after the transaction block
        if commit:
            db.commit()

        rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows

    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None

MENTION_PATTERN = re.compile(r'(?<!\w)@([A-Za-z0-9_]{1,50})\b')


def get_tagged_users(content):
    """Find existing users mentioned with @username."""

    if not content:
        return []

    usernames = MENTION_PATTERN.findall(content)

    # Remove duplicate usernames
    usernames = list(dict.fromkeys(usernames))

    tagged_users = []

    for username in usernames:
        user = query_db(
            '''
            SELECT id, username
            FROM users
            WHERE username = ?
            ''',
            (username,),
            one=True
        )

        if user is not None:
            tagged_users.append(user)

    return tagged_users



def create_tag_notifications(
        tagger_id,
        source_type,
        source_id,
        post_id=None,
        comment_id=None
):
    """Create tag records and notifications for @mentions."""

    # Get the content that contains the @mentions
    db = get_db()

    if source_type == 'post':
        cursor = db.execute(
            'SELECT content FROM posts WHERE id = ?',
            (source_id,)
        )

    elif source_type == 'comment':
        cursor = db.execute(
            'SELECT content FROM comments WHERE id = ?',
            (source_id,)
        )

    else:
        return

    source = cursor.fetchone()

    if source is None:
        return

    content = source[0]

    tagged_users = get_tagged_users(content)

    db = get_db()

    for tagged_user in tagged_users:

        # Don't notify someone if they tag themselves
        if not tagged_user:
            continue

        if tagged_user['id'] == tagger_id:
            continue

        blocked = query_db(
            '''
            SELECT 1 FROM tag_blocks
            WHERE blocker_id = ? AND blocked_user_id = ?
            ''',
            (tagged_user['id'], tagger_id),
            one=True
        )

        if blocked:
            continue
        
        # Don't create the same tag twice
        existing_tag = query_db(
            '''
            SELECT id
            FROM tags
            WHERE source_type = ?
              AND source_id = ?
              AND tagged_user_id = ?
            ''',
            (
                source_type,
                source_id,
                tagged_user['id']
            ),
            one=True
        )

        if existing_tag:
            continue

        # Create the tag
        cursor = db.execute(
            '''
            INSERT INTO tags
            (
                tagger_id,
                tagged_user_id,
                source_type,
                source_id
            )
            VALUES (?, ?, ?, ?)
            ''',
            (
                tagger_id,
                tagged_user['id'],
                source_type,
                source_id
            )
        )

        tag_id = cursor.lastrowid

        # Create the notification
        db.execute(
            '''
            INSERT INTO notifications
            (
                recipient_id,
                actor_id,
                type,
                tag_id,
                post_id,
                comment_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (
                tagged_user['id'],
                tagger_id,
                'tag',
                tag_id,
                post_id,
                comment_id
            )
        )

    db.commit()

@app.route('/notifications/<int:notification_id>/read')
def mark_notification_read(notification_id):
    user_id = session.get('user_id')

    if not user_id:
        return redirect(url_for('login'))

    query_db(
        """
        UPDATE notifications
        SET is_read = 1
        WHERE id = ?
          AND recipient_id = ?
        """,
        (notification_id, user_id),
        commit=True
    )

    db = get_db()

    cursor = db.execute(
        """
        SELECT post_id, comment_id
        FROM notifications
        WHERE id = ?
            AND recipient_id = ?
        """,
    (notification_id, user_id)
    )

    notification = cursor.fetchone()

    if notification is None:
        return redirect(url_for('feed'))

    if notification is None:
        return redirect(url_for('feed'))

    if notification[1]:
        return redirect(
            url_for(
                'post_detail',
                post_id=notification[0]
            )
        )

    if notification[0]:
        return redirect(
            url_for(
                'post_detail',
                post_id=notification[0]
            )
        )

    return redirect(url_for('feed'))

@app.route('/notifications/block/<int:actor_id>', methods=['POST'])
def block_tag_notification(actor_id):
    """Silently blocks a user from tagging the current user in the future."""
    user_id = session.get('user_id')

    if not user_id:
        return redirect(url_for('login'))

    if actor_id == user_id:
        flash("You cannot block yourself.", "warning")
        return redirect(request.referrer or url_for('feed'))

    actor = query_db(
        'SELECT id FROM users WHERE id = ?',
        (actor_id,),
        one=True
    )

    if not actor:
        abort(404)

    query_db(
        '''
        INSERT OR IGNORE INTO tag_blocks
            (blocker_id, blocked_user_id)
        VALUES (?, ?)
        ''',
        (user_id, actor_id),
        commit=True
    )

    flash(
        'Tag notifications from this user are now blocked.',
        'success'
    )
    return redirect(request.referrer or url_for('feed'))

@app.route('/notifications/unblock/<int:actor_id>', methods=['POST'])
def unblock_tag_notification(actor_id):
    """Unblock a user"""
    user_id = session.get('user_id')

    if not user_id:
        return redirect(url_for('login'))

    query_db(
        '''
        DELETE FROM tag_blocks
        WHERE blocker_id = ? AND blocked_user_id = ?
        ''',
        (user_id, actor_id),
        commit=True
    )

    flash(
        'Tag notifications from this user are now allowed again.',
        'success'
    )
    return redirect(request.referrer or url_for('feed'))


@app.template_filter('datetimeformat')
def datetimeformat(value):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    else:
        return "N/A"
    return dt.strftime('%b %d, %Y %H:%M')

REACTION_EMOJIS = {
    'like': '❤️', 'love': '😍', 'laugh': '😂',
    'wow': '😮', 'sad': '😢', 'angry': '😠',
}
REACTION_TYPES = list(REACTION_EMOJIS.keys())


@app.route('/')
def feed():
    #  1. Get Pagination and Filter Parameters 
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1
    sort = request.args.get('sort', 'new').lower()
    show = request.args.get('show', 'all').lower()
    
    # Define how many posts to show per page
    POSTS_PER_PAGE = 10
    offset = (page - 1) * POSTS_PER_PAGE

    current_user_id = session.get('user_id')
    
    notifications = []

    if current_user_id:
        notifications = query_db(
    """
    SELECT
        n.id,
        n.type,
        n.is_read,
        n.created_at,
        n.actor_id AS actor_id,

        actor.username AS actor_username,

        n.post_id,
        n.comment_id,

        p.content AS post_content,
        c.content AS comment_content

    FROM notifications n

    JOIN tags t
        ON t.id = n.tag_id

    JOIN users actor
        ON actor.id = t.tagger_id

    JOIN posts p
        ON p.id = n.post_id

    LEFT JOIN comments c
        ON c.id = n.comment_id

    WHERE n.recipient_id = ?
        AND n.is_read = 0
        AND NOT EXISTS (
            SELECT 1
            FROM tag_blocks tb
            WHERE tb.blocker_id = n.recipient_id
                AND tb.blocked_user_id = n.actor_id
        )
        
    ORDER BY n.created_at DESC

    LIMIT 20
    """,
    (current_user_id,)
)

    params = []

    #  2. Build the Query 
    where_clause = ""
    if show == 'following' and current_user_id:
        where_clause = "WHERE p.user_id IN (SELECT followed_id FROM follows WHERE follower_id = ?)"
        params.append(current_user_id)

    # Add the pagination parameters to the query arguments
    pagination_params = (POSTS_PER_PAGE, offset)

    if sort == 'popular':
        query = f"""
            SELECT p.id, p.content, p.created_at, u.username, u.id as user_id,
                   IFNULL(r.total_reactions, 0) as total_reactions
            FROM posts p
            JOIN users u ON p.user_id = u.id
            LEFT JOIN (
                SELECT post_id, COUNT(*) as total_reactions FROM reactions GROUP BY post_id
            ) r ON p.id = r.post_id
            {where_clause}
            ORDER BY total_reactions DESC, p.created_at DESC
            LIMIT ? OFFSET ?
        """
        final_params = params + list(pagination_params)
        posts = query_db(query, final_params)
    elif sort == 'recommended':
        posts = recommend(current_user_id, show == 'following' and current_user_id)
    else:  # Default sort is 'new'
        query = f"""
            SELECT p.id, p.content, p.created_at, u.username, u.id as user_id
            FROM posts p
            JOIN users u ON p.user_id = u.id
            {where_clause}
            ORDER BY p.created_at DESC
            LIMIT ? OFFSET ?
        """
        final_params = params + list(pagination_params)
        posts = query_db(query, final_params)

    posts_data = []
    for post in posts:
        # Determine if the current user follows the poster
        followed_poster = False
        if current_user_id and post['user_id'] != current_user_id:
            follow_check = query_db(
                'SELECT 1 FROM follows WHERE follower_id = ? AND followed_id = ?',
                (current_user_id, post['user_id']),
                one=True
            )
            if follow_check:
                followed_poster = True

        # Determine if the current user reacted to this post and with what reaction
        user_reaction = None
        if current_user_id:
            reaction_check = query_db(
                'SELECT reaction_type FROM reactions WHERE user_id = ? AND post_id = ?',
                (current_user_id, post['id']),
                one=True
            )
            if reaction_check:
                user_reaction = reaction_check['reaction_type']

        reactions = query_db('SELECT reaction_type, COUNT(*) as count FROM reactions WHERE post_id = ? GROUP BY reaction_type', (post['id'],))
        comments_raw = query_db('SELECT c.id, c.content, c.created_at, u.username, u.id as user_id FROM comments c JOIN users u ON c.user_id = u.id WHERE c.post_id = ? ORDER BY c.created_at ASC', (post['id'],))
        post_dict = dict(post)
        post_dict['content'], _ = moderate_content(post_dict['content'])
        comments_moderated = []
        for comment in comments_raw:
            comment_dict = dict(comment)
            comment_dict['content'], _ = moderate_content(comment_dict['content'])
            comments_moderated.append(comment_dict)
        posts_data.append({
            'post': post_dict,
            'reactions': reactions,
            'user_reaction': user_reaction,
            'followed_poster': followed_poster,
            'comments': comments_moderated
        })

    #  4. Render Template with Pagination Info 
    return render_template('feed.html.j2', 
                           posts=posts_data, 
                           current_sort=sort,
                           current_show=show,
                           page=page, # Pass current page number
                           per_page=POSTS_PER_PAGE, # Pass items per page
                           reaction_emojis=REACTION_EMOJIS,
                           reaction_types=REACTION_TYPES,
                           notifications=notifications)

@app.route('/posts/new', methods=['POST'])
def add_post():
    """Handles creating a new post from the feed."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to create a post.', 'danger')
        return redirect(url_for('login'))

    # Get content from the submitted form
    content = request.form.get('content')

    # Pass the user's content through the moderation function
    moderated_content = content

    # Basic validation to ensure post is not empty
    if moderated_content and moderated_content.strip():
        db = get_db()

        cursor = db.execute(
            'INSERT INTO posts (user_id, content) VALUES (?, ?)',
            (user_id, moderated_content)
        )

        post_id = cursor.lastrowid

        db.commit()

        create_tag_notifications(
            tagger_id=user_id,
            source_type='post',
            source_id=post_id,
            post_id=post_id
        )

        flash('Your post was successfully created!', 'success')
    else:
        # This will catch empty posts or posts that were fully censored
        flash('Post cannot be empty or was fully censored.', 'warning')

    # Redirect back to the main feed to see the new post
    return redirect(url_for('feed'))
    
    
@app.route('/posts/<int:post_id>/delete', methods=['POST'])
def delete_post(post_id):
    """Handles deleting a post."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to delete a post.', 'danger')
        return redirect(url_for('login'))

    # Find the post in the database
    post = query_db('SELECT id, user_id FROM posts WHERE id = ?', (post_id,), one=True)

    # Check if the post exists and if the current user is the owner
    if not post:
        flash('Post not found.', 'danger')
        return redirect(url_for('feed'))

    if post['user_id'] != user_id:
        # Security check: prevent users from deleting others' posts
        flash('You do not have permission to delete this post.', 'danger')
        return redirect(url_for('feed'))

    # If all checks pass, proceed with deletion
    db = get_db()
    # To maintain database integrity, delete associated records first
    db.execute('DELETE FROM comments WHERE post_id = ?', (post_id,))
    db.execute('DELETE FROM reactions WHERE post_id = ?', (post_id,))
    # Finally, delete the post itself
    db.execute('DELETE FROM posts WHERE id = ?', (post_id,))
    db.commit()

    flash('Your post was successfully deleted.', 'success')
    # Redirect back to the page the user came from, or the feed as a fallback
    return redirect(request.referrer or url_for('feed'))

@app.route('/my-profile/update', methods=['POST'])
def update_my_profile():
    user_id = session.get('user_id')

    if not user_id:
        flash('Please log in to edit your profile.', 'warning')
        return redirect(url_for('login'))

    display_name = (
        request.form.get('display_name') or ''
    ).strip()

    if len(display_name) > 50:
         flash('Display name cannot exceed 50 characters.', 'danger') 
         return redirect(
             url_for('user_profile', username=session.get('username'))
         )  

    current_user = query_db(
        'SELECT username, profile_picture FROM users WHERE id = ?',
        (user_id,),
        one=True
    )

    if not current_user:
        session.clear()
        return redirect(url_for('login'))

    picture_name = current_user['profile_picture']
    uploaded = request.files.get('profile_picture')

    if uploaded and uploaded.filename:
        safe_name = secure_filename(uploaded.filename)
        extension = (
            safe_name.rsplit('.', 1)[-1].lower()
            if '.' in safe_name else ''
        )

        if extension not in ALLOWED_PROFILE_PICTURE_EXTENSIONS:
            flash('Invalid file type for profile picture.', 'danger')
            return redirect(
                url_for('user_profile', username=current_user['username'])
            )

        upload_dir = os.path.join(app.root_path, 'static', 'uploads', 'profile_pictures')
        os.makedirs(upload_dir, exist_ok=True)

        picture_name = f"user_{user_id}.{extension}"
        uploaded.save(os.path.join(upload_dir, picture_name))
        picture_name = f"uploads/profile_pictures/{picture_name}"

    query_db(
        'UPDATE users SET display_name = ?, profile_picture = ? WHERE id = ?',
        (display_name or None, picture_name, user_id),
        commit=True
    )

    flash('Your profile has been updated.', 'success')
    return redirect(url_for('user_profile', username=current_user['username']))

@app.route('/u/<username>')
def user_profile(username):
    """Displays a user's profile page with moderated bio, posts, and latest comments."""
    
    user_raw = query_db('SELECT * FROM users WHERE username = ?', (username,), one=True)
    if not user_raw:
        abort(404)

    user = dict(user_raw)
    moderated_bio, _ = moderate_content(user.get('profile', ''))
    user['profile'] = moderated_bio

    posts_raw = query_db('SELECT id, content, user_id, created_at FROM posts WHERE user_id = ? ORDER BY created_at DESC', (user['id'],))
    posts = []
    for post_raw in posts_raw:
        post = dict(post_raw)
        moderated_post_content, _ = moderate_content(post['content'])
        post['content'] = moderated_post_content
        posts.append(post)

    comments_raw = query_db('SELECT id, content, user_id, post_id, created_at FROM comments WHERE user_id = ? ORDER BY created_at DESC LIMIT 100', (user['id'],))
    comments = []
    for comment_raw in comments_raw:
        comment = dict(comment_raw)
        moderated_comment_content, _ = moderate_content(comment['content'])
        comment['content'] = moderated_comment_content
        comments.append(comment)

    followers_count = query_db('SELECT COUNT(*) as cnt FROM follows WHERE followed_id = ?', (user['id'],), one=True)['cnt']
    following_count = query_db('SELECT COUNT(*) as cnt FROM follows WHERE follower_id = ?', (user['id'],), one=True)['cnt']

    #  NEW: CHECK FOLLOW STATUS 
    is_currently_following = False # Default to False
    current_user_id = session.get('user_id')
    
    # We only need to check if a user is logged in
    if current_user_id:
        follow_relation = query_db(
            'SELECT 1 FROM follows WHERE follower_id = ? AND followed_id = ?',
            (current_user_id, user['id']),
            one=True
        )
        if follow_relation:
            is_currently_following = True
    # --

    return render_template('user_profile.html.j2', 
                           user=user, 
                           posts=posts, 
                           comments=comments,
                           followers_count=followers_count, 
                           following_count=following_count,
                           is_following=is_currently_following)
    

@app.route('/u/<username>/followers')
def user_followers(username):
    user = query_db('SELECT * FROM users WHERE username = ?', (username,), one=True)
    if not user:
        abort(404)
    followers = query_db('''
        SELECT u.username
        FROM follows f
        JOIN users u ON f.follower_id = u.id
        WHERE f.followed_id = ?
    ''', (user['id'],))
    return render_template('user_list.html.j2', user=user, users=followers, title="Followers of")

@app.route('/u/<username>/following')
def user_following(username):
    user = query_db('SELECT * FROM users WHERE username = ?', (username,), one=True)
    if not user:
        abort(404)
    following = query_db('''
        SELECT u.username
        FROM follows f
        JOIN users u ON f.followed_id = u.id
        WHERE f.follower_id = ?
    ''', (user['id'],))
    return render_template('user_list.html.j2', user=user, users=following, title="Users followed by")

@app.route('/posts/<int:post_id>')
def post_detail(post_id):
    """Displays a single post and its comments, with content moderation applied."""
    
    post_raw = query_db('''
        SELECT p.id, p.content, p.created_at, u.username, u.id as user_id
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.id = ?
    ''', (post_id,), one=True)

    if not post_raw:
        # The abort function will stop the request and show a 404 Not Found page.
        abort(404)

    #  Moderation for the Main Post 
    # Convert the raw database row to a mutable dictionary
    post = dict(post_raw)
    # Unpack the tuple from moderate_content, we only need the moderated content string here
    moderated_post_content, _ = moderate_content(post['content'])
    post['content'] = moderated_post_content

    #  Fetch Reactions (No moderation needed) 
    reactions = query_db('''
        SELECT reaction_type, COUNT(*) as count
        FROM reactions
        WHERE post_id = ?
        GROUP BY reaction_type
    ''', (post_id,))

    #  Fetch and Moderate Comments 
    comments_raw = query_db('SELECT c.id, c.content, c.created_at, u.username, u.id as user_id FROM comments c JOIN users u ON c.user_id = u.id WHERE c.post_id = ? ORDER BY c.created_at ASC', (post_id,))
    
    comments = [] # Create a new list for the moderated comments
    for comment_raw in comments_raw:
        comment = dict(comment_raw) # Convert to a dictionary
        # Moderate the content of each comment
        print(comment['content'])
        moderated_comment_content, _ = moderate_content(comment['content'])
        comment['content'] = moderated_comment_content
        comments.append(comment)

    # Pass the moderated data to the template
    return render_template('post_detail.html.j2',
                           post=post,
                           reactions=reactions,
                           comments=comments,
                           reaction_emojis=REACTION_EMOJIS,
                           reaction_types=REACTION_TYPES)

@app.route('/about')
def about():
    return render_template('about.html.j2')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html.j2')

@app.route('/tutorial')
def tutorial():
    return render_template('tutorial.html.j2', title= 'Tutorial')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        location = request.form.get('location', '')
        birthdate = request.form.get('birthdate', '')
        profile = request.form.get('profile', '')

        hashed_password = generate_password_hash(password)

        db = get_db()
        cur = db.cursor()
        try:
            cur.execute(
                'INSERT INTO users (username, password, location, birthdate, profile) VALUES (?, ?, ?, ?, ?)',
                (username, hashed_password, location, birthdate, profile)
            )
            db.commit()

            # 1. Get the ID of the user we just created.
            new_user_id = cur.lastrowid

            # 2. Add user info to the session cookie.
            session.clear() # Clear any old session data
            session['user_id'] = new_user_id
            session['username'] = username

            # 3. Flash a welcome message and redirect to the feed.
            flash(f'Welcome, {username}! Your account has been created.', 'success')
            return redirect(url_for('feed')) # Redirect to the main feed/dashboard

        except sqlite3.IntegrityError:
            flash('Username already taken. Please choose another one.', 'danger')
        finally:
            cur.close()
            db.close()
            
    return render_template('signup.html.j2')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        db.close()

        # 1. Check if the user exists.
        # 2. If user exists, use check_password_hash to securely compare the password.
        #    This function handles the salt and prevents timing attacks.
        if user and check_password_hash(user['password'], password):
            # Password is correct!
            session['user_id'] = user['id']
            session['username'] = user['username']
            flash('Logged in successfully.', 'success')
            return redirect(url_for('feed'))
        else:
            # User does not exist or password was incorrect.
            flash('Invalid username or password.', 'danger')
            
    return render_template('login.html.j2')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/posts/<int:post_id>/comment', methods=['POST'])
def add_comment(post_id):
    """Handles adding a new comment to a specific post."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to comment.', 'danger')
        return redirect(url_for('login'))

    # Get content from the submitted form
    content = request.form.get('content')

    # Basic validation to ensure comment is not empty
    if content and content.strip():
        db = get_db()

        cursor = db.execute(
            """
            INSERT INTO comments 
            (post_id, user_id, content)
            VALUES (?, ?, ?)
            """,
            (post_id, user_id, content)
        )

        comment_id = cursor.lastrowid

        create_tag_notifications(
            tagger_id=user_id,
            source_type='comment',
            source_id=comment_id,
            post_id=post_id,
            comment_id=comment_id
        )
       
        flash('Your comment was added.', 'success')
    else:
        flash('Comment cannot be empty.', 'warning')

    # Redirect back to the page the user came from (likely the post detail page)
    return redirect(request.referrer or url_for('post_detail', post_id=post_id))

@app.route('/comments/<int:comment_id>/delete', methods=['POST'])
def delete_comment(comment_id):
    """Handles deleting a comment."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to delete a comment.', 'danger')
        return redirect(url_for('login'))

    # Find the comment and the original post's author ID
    comment = query_db('''
        SELECT c.id, c.user_id, p.user_id as post_author_id
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        WHERE c.id = ?
    ''', (comment_id,), one=True)

    # Check if the comment exists
    if not comment:
        flash('Comment not found.', 'danger')
        return redirect(request.referrer or url_for('feed'))

    # Security Check: Allow deletion if the user is the comment's author OR the post's author
    if user_id != comment['user_id'] and user_id != comment['post_author_id']:
        flash('You do not have permission to delete this comment.', 'danger')
        return redirect(request.referrer or url_for('feed'))

    # If all checks pass, proceed with deletion
    db = get_db()
    db.execute('DELETE FROM comments WHERE id = ?', (comment_id,))
    db.commit()

    flash('Comment successfully deleted.', 'success')
    # Redirect back to the page the user came from
    return redirect(request.referrer or url_for('feed'))

@app.route('/react', methods=['POST'])
def add_reaction():
    """Handles adding a new reaction or updating an existing one."""
    user_id = session.get('user_id')

    if not user_id:
        flash("You must be logged in to react.", "danger")
        return redirect(url_for('login'))

    post_id = request.form.get('post_id')
    new_reaction_type = request.form.get('reaction')

    if not post_id or not new_reaction_type:
        flash("Invalid reaction request.", "warning")
        return redirect(request.referrer or url_for('feed'))

    db = get_db()

    # Step 1: Check if a reaction from this user already exists on this post.
    existing_reaction = query_db('SELECT id FROM reactions WHERE post_id = ? AND user_id = ?',
                                 (post_id, user_id), one=True)

    if existing_reaction:
        # Step 2: If it exists, UPDATE the reaction_type.
        db.execute('UPDATE reactions SET reaction_type = ? WHERE id = ?',
                   (new_reaction_type, existing_reaction['id']))
    else:
        # Step 3: If it does not exist, INSERT a new reaction.
        db.execute('INSERT INTO reactions (post_id, user_id, reaction_type) VALUES (?, ?, ?)',
                   (post_id, user_id, new_reaction_type))

    db.commit()

    return redirect(request.referrer or url_for('feed'))

@app.route('/unreact', methods=['POST'])
def unreact():
    """Handles removing a user's reaction from a post."""
    user_id = session.get('user_id')

    if not user_id:
        flash("You must be logged in to unreact.", "danger")
        return redirect(url_for('login'))

    post_id = request.form.get('post_id')

    if not post_id:
        flash("Invalid unreact request.", "warning")
        return redirect(request.referrer or url_for('feed'))

    db = get_db()

    # Remove the reaction if it exists
    existing_reaction = query_db(
        'SELECT id FROM reactions WHERE post_id = ? AND user_id = ?',
        (post_id, user_id),
        one=True
    )

    if existing_reaction:
        db.execute('DELETE FROM reactions WHERE id = ?', (existing_reaction['id'],))
        db.commit()
        flash("Reaction removed.", "success")
    else:
        flash("No reaction to remove.", "info")

    return redirect(request.referrer or url_for('feed'))


@app.route('/u/<int:user_id>/follow', methods=['POST'])
def follow_user(user_id):
    """Handles the logic for the current user to follow another user."""
    follower_id = session.get('user_id')

    # Security: Ensure user is logged in
    if not follower_id:
        flash("You must be logged in to follow users.", "danger")
        return redirect(url_for('login'))

    # Security: Prevent users from following themselves
    if follower_id == user_id:
        flash("You cannot follow yourself.", "warning")
        return redirect(request.referrer or url_for('feed'))

    # Check if the user to be followed actually exists
    user_to_follow = query_db('SELECT id FROM users WHERE id = ?', (user_id,), one=True)
    if not user_to_follow:
        flash("The user you are trying to follow does not exist.", "danger")
        return redirect(request.referrer or url_for('feed'))
        
    db = get_db()
    try:
        # Insert the follow relationship. The PRIMARY KEY constraint will prevent duplicates if you've set one.
        db.execute('INSERT INTO follows (follower_id, followed_id) VALUES (?, ?)',
                   (follower_id, user_id))
        db.commit()
        username_to_follow = query_db('SELECT username FROM users WHERE id = ?', (user_id,), one=True)['username']
        flash(f"You are now following {username_to_follow}.", "success")
    except sqlite3.IntegrityError:
        flash("You are already following this user.", "info")

    return redirect(request.referrer or url_for('feed'))


@app.route('/u/<int:user_id>/unfollow', methods=['POST'])
def unfollow_user(user_id):
    """Handles the logic for the current user to unfollow another user."""
    follower_id = session.get('user_id')

    # Security: Ensure user is logged in
    if not follower_id:
        flash("You must be logged in to unfollow users.", "danger")
        return redirect(url_for('login'))

    db = get_db()
    cur = db.execute('DELETE FROM follows WHERE follower_id = ? AND followed_id = ?',
               (follower_id, user_id))
    db.commit()

    if cur.rowcount > 0:
        # cur.rowcount tells us if a row was actually deleted
        username_unfollowed = query_db('SELECT username FROM users WHERE id = ?', (user_id,), one=True)['username']
        flash(f"You have unfollowed {username_unfollowed}.", "success")
    else:
        # This case handles if someone tries to unfollow a user they weren't following
        flash("You were not following this user.", "info")

    # Redirect back to the page the user came from
    return redirect(request.referrer or url_for('feed'))

@app.route('/admin')
def admin_dashboard():
    """Displays the admin dashboard with users, posts, and comments, sorted by risk."""

    if session.get('username') != 'admin':
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for('feed'))

    RISK_LEVELS = { "HIGH": 5, "MEDIUM": 3, "LOW": 1 }
    PAGE_SIZE = 50

    def get_risk_profile(score):
        if score >= RISK_LEVELS["HIGH"]:
            return "HIGH", 3
        elif score >= RISK_LEVELS["MEDIUM"]:
            return "MEDIUM", 2
        elif score >= RISK_LEVELS["LOW"]:
            return "LOW", 1
        return "NONE", 0

    # Get pagination and current tab parameters
    try:
        users_page = int(request.args.get('users_page', 1))
        posts_page = int(request.args.get('posts_page', 1))
        comments_page = int(request.args.get('comments_page', 1))
    except ValueError:
        users_page = 1
        posts_page = 1
        comments_page = 1
    
    current_tab = request.args.get('tab', 'users') # Default to 'users' tab

    users_offset = (users_page - 1) * PAGE_SIZE
    
    # First, get all users to calculate risk, then apply pagination in Python
    # It's more complex to do this efficiently in SQL if risk calc is Python-side
    all_users_raw = query_db('SELECT id, username, profile, created_at FROM users')
    all_users = []
    for user in all_users_raw:
        user_dict = dict(user)
        user_risk_score = user_risk_analysis(user_dict['id'])
        risk_label, risk_sort_key = get_risk_profile(user_risk_score)
        user_dict['risk_label'] = risk_label
        user_dict['risk_sort_key'] = risk_sort_key
        user_dict['risk_score'] = min(5.0, round(user_risk_score, 2))
        all_users.append(user_dict)

    all_users.sort(key=lambda x: x['risk_score'], reverse=True)
    total_users = len(all_users)
    users = all_users[users_offset : users_offset + PAGE_SIZE]
    total_users_pages = (total_users + PAGE_SIZE - 1) // PAGE_SIZE

    # --- Posts Tab Data ---
    posts_offset = (posts_page - 1) * PAGE_SIZE
    total_posts_count = query_db('SELECT COUNT(*) as count FROM posts', one=True)['count']
    total_posts_pages = (total_posts_count + PAGE_SIZE - 1) // PAGE_SIZE

    posts_raw = query_db(f'''
        SELECT p.id, p.content, p.created_at, u.username, u.created_at as user_created_at
        FROM posts p JOIN users u ON p.user_id = u.id
        ORDER BY p.id DESC -- Order by ID for consistent pagination before risk sort
        LIMIT ? OFFSET ?
    ''', (PAGE_SIZE, posts_offset))
    posts = []
    for post in posts_raw:
        post_dict = dict(post)
        _, base_score = moderate_content(post_dict['content'])
        final_score = base_score 
        author_created_dt = post_dict['user_created_at']
        author_age_days = (datetime.utcnow() - author_created_dt).days
        if author_age_days < 7:
            final_score *= 1.5
        risk_label, risk_sort_key = get_risk_profile(final_score)
        post_dict['risk_label'] = risk_label
        post_dict['risk_sort_key'] = risk_sort_key
        post_dict['risk_score'] = round(final_score, 2)
        posts.append(post_dict)

    posts.sort(key=lambda x: x['risk_score'], reverse=True) # Sort after fetching and scoring

    # --- Comments Tab Data ---
    comments_offset = (comments_page - 1) * PAGE_SIZE
    total_comments_count = query_db('SELECT COUNT(*) as count FROM comments', one=True)['count']
    total_comments_pages = (total_comments_count + PAGE_SIZE - 1) // PAGE_SIZE

    comments_raw = query_db(f'''
        SELECT c.id, c.content, c.created_at, u.username, u.created_at as user_created_at
        FROM comments c JOIN users u ON c.user_id = u.id
        ORDER BY c.id DESC -- Order by ID for consistent pagination before risk sort
        LIMIT ? OFFSET ?
    ''', (PAGE_SIZE, comments_offset))
    comments = []
    for comment in comments_raw:
        comment_dict = dict(comment)
        _, score = moderate_content(comment_dict['content'])
        author_created_dt = comment_dict['user_created_at']
        author_age_days = (datetime.utcnow() - author_created_dt).days
        if author_age_days < 7:
            score *= 1.5
        risk_label, risk_sort_key = get_risk_profile(score)
        comment_dict['risk_label'] = risk_label
        comment_dict['risk_sort_key'] = risk_sort_key
        comment_dict['risk_score'] = round(score, 2)
        comments.append(comment_dict)

    comments.sort(key=lambda x: x['risk_score'], reverse=True) # Sort after fetching and scoring


    return render_template('admin.html.j2', 
                           users=users, 
                           posts=posts, 
                           comments=comments,
                           
                           # Pagination for Users
                           users_page=users_page,
                           total_users_pages=total_users_pages,
                           users_has_next=(users_page < total_users_pages),
                           users_has_prev=(users_page > 1),

                           # Pagination for Posts
                           posts_page=posts_page,
                           total_posts_pages=total_posts_pages,
                           posts_has_next=(posts_page < total_posts_pages),
                           posts_has_prev=(posts_page > 1),

                           # Pagination for Comments
                           comments_page=comments_page,
                           total_comments_pages=total_comments_pages,
                           comments_has_next=(comments_page < total_comments_pages),
                           comments_has_prev=(comments_page > 1),

                           current_tab=current_tab,
                           PAGE_SIZE=PAGE_SIZE)



@app.route('/admin/delete/user/<int:user_id>', methods=['POST'])
def admin_delete_user(user_id):
    if session.get('username') != 'admin':
        flash("You do not have permission to perform this action.", "danger")
        return redirect(url_for('feed'))
        
    if user_id == session.get('user_id'):
        flash('You cannot delete your own account from the admin panel.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    db = get_db()
    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()
    flash(f'User {user_id} and all their content has been deleted.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete/post/<int:post_id>', methods=['POST'])
def admin_delete_post(post_id):
    if session.get('username') != 'admin':
        flash("You do not have permission to perform this action.", "danger")
        return redirect(url_for('feed'))

    db = get_db()
    db.execute('DELETE FROM comments WHERE post_id = ?', (post_id,))
    db.execute('DELETE FROM reactions WHERE post_id = ?', (post_id,))
    db.execute('DELETE FROM posts WHERE id = ?', (post_id,))
    db.commit()
    flash(f'Post {post_id} has been deleted.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete/comment/<int:comment_id>', methods=['POST'])
def admin_delete_comment(comment_id):
    if session.get('username') != 'admin':
        flash("You do not have permission to perform this action.", "danger")
        return redirect(url_for('feed'))

    db = get_db()
    db.execute('DELETE FROM comments WHERE id = ?', (comment_id,))
    db.commit()
    flash(f'Comment {comment_id} has been deleted.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/rules')
def rules():
    return render_template('rules.html.j2')

@app.template_global()
def loop_color(user_id):
    # Generate a pastel color based on user_id hash
    h = hashlib.md5(str(user_id).encode()).hexdigest()
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return f'rgb({r % 128 + 80}, {g % 128 + 80}, {b % 128 + 80})'


# ----- Functions to be implemented are below
# Coding Assignment #2

# Assignment 2.2
def user_risk_analysis(user_id):
    """
    Args:
        user_id: The ID of the user on which we perform risk analysis.

    Returns:
        A float number score showing the risk associated with this user. There are no strict rules or bounds to this score, other than that a score of less than 1.0 means no risk, 1.0 to 3.0 is low risk, 3.0 to 5.0 is medium risk and above 5.0 is high risk. (An upper bound of 5.0 is applied to this score elsewhere in the codebase) 
        
        You will be able to check the scores by logging in with the administrator account:
            username: admin
            password: admin
        Then, navigate to the /admin endpoint. (http://localhost:8080/admin)
    """

    user = query_db(
        'SELECT id, profile, created_at FROM users WHERE id = ?',
        (user_id,),
        one=True
    )

    if user is None:
        return 0.0

    account_age_days = (
        datetime.utcnow() - user['created_at']
    ).days

    _, profile_score = moderate_content(user['profile'] or '')

    posts = query_db(
        'SELECT content FROM posts WHERE user_id = ?',
        (user_id,)
    ) or []

    post_scores = []

    for post in posts:
        _, post_score = moderate_content(post['content'] or '')
        post_scores.append(post_score)

    average_post_score = (
        sum(post_scores) / len(post_scores)
        if post_scores else 0.0
    )

    comments = query_db(
        'SELECT content FROM comments WHERE user_id = ?',
        (user_id,)
    ) or []

    comments_scores = []

    for comment in comments:
        _, comment_score = moderate_content(comment['content'] or '')
        comments_scores.append(comment_score)

    average_comment_score = (
        sum(comments_scores) / len(comments_scores)
        if comments_scores else 0.0
    )

    content_risk_score = (
        profile_score
        + average_post_score * 3
        + average_comment_score
    )

    if account_age_days < 7:
        content_risk_score *= 1.5
    elif account_age_days < 30:
        content_risk_score *= 1.2

    user_risk_score = min(5.0, content_risk_score)
    
    return float(user_risk_score)
    
# Assignment 2.1
def moderate_content(content):
    """
    Args
        content: the text content of a post or comment to be moderated.
        
    Returns: 
        A tuple containing the moderated content (string) and a severity score (float). There are no strict rules or bounds to the severity score, other than that a score of less than 1.0 means no risk, 1.0 to 3.0 is low risk, 3.0 to 5.0 is medium risk and above 5.0 is high risk.
    
    This function moderates a string of content and calculates a severity score based on
    rules loaded from the 'censorship.dat' file. These are already loaded as TIER1_WORDS, TIER2_PHRASES and TIER3_WORDS. Tier 1 corresponds to strong profanity, Tier 2 to scam/spam phrases and Tier 3 to mild profanity.
    
    You will be able to check the scores by logging in with the administrator account:
            username: admin
            password: admin
    Then, navigate to the /admin endpoint. (http://localhost:8080/admin)
    """

    moderated_content = content
    score = 0
    
    for word in TIER1_WORDS:
        pattern = r'\b' + re.escape(word) + r'\b'

        if re.search(pattern, content, re.IGNORECASE):
            return "[content removed due to severe violation]", 5.0

    for phrase in TIER2_PHRASES:
        pattern = r'\b' + re.escape(phrase) + r'\b'

        if re.search(pattern, content, re.IGNORECASE):
            return "[content removed due to spam/scam policy]", 5.0

    for word in TIER3_WORDS:
        pattern = r'\b' + re.escape(word) + r'\b'

        matches = re.findall(pattern, moderated_content, re.IGNORECASE)
        score += len(matches) * 2.0

        moderated_content = re.sub(
            pattern,
            lambda match: '*' * len(match.group()),
            moderated_content,
            flags=re.IGNORECASE
        )

    url_pattern = r'https?://\S+|www\.\S+'

    urls = re.findall(url_pattern, moderated_content, re.IGNORECASE)
    score += len(urls) * 2.0

    moderated_content = re.sub(
        url_pattern,
        '[link removed]',
        moderated_content,
        flags=re.IGNORECASE
    )

    letters = [char for char in moderated_content if char.isalpha()]

    if len(letters) > 15:
        uppercase_count = sum(1 for char in letters if char.isupper())

        if uppercase_count / len(letters) > 0.70:
            score += 0.5

    return moderated_content, score

# Coding Assignment #3
# Assignment 3.1
def recommend(user_id, filter_following):
    """
    Args:
        user_id: The ID of the current user.
        filter_following: Boolean, True if we only want to see recommendations from followed users.

    Returns:
        A list of 5 recommended posts, in reverse-chronological order.

    To test whether your recommendation algorithm works, let's pretend we like the DIY topic. Here are some users that often post DIY comment and a few example posts. Make sure your account did not engage with anything else. You should test your algorithm with these and see if your recommendation algorithm picks up on your interest in DIY and starts showing related content.
    
    Users: @starboy99, @DancingDolphin, @blogger_bob
    Posts: 1810, 1875, 1880, 2113
    
    Materials: 
    - https://www.nvidia.com/en-us/glossary/recommendation-system/
    - http://www.configworks.com/mz/handout_recsys_sac2010.pdf
    - https://www.researchgate.net/publication/227268858_Recommender_Systems_Handbook
    """
    
    if not user_id:
        return []

    positive_reactions = ('like', 'love', 'laugh')

    stop_words = {
        'the', 'and', 'for', 'are', 'but', 'not', 'you',
        'your', 'with', 'this', 'that', 'have', 'has',
        'was', 'were', 'from', 'they', 'their', 'what',
        'when', 'where', 'who', 'will', 'would', 'can',
        'just', 'about', 'into', 'its', 'our', 'out',
        'all', 'get', 'got', 'how', 'his', 'her', 'she',
        'him', 'them', 'then', 'than', 'too', 'very',
        'been', 'being', 'some', 'more', 'one', 'like',
        'love', 'lol', 'www', 'http', 'https'
    }

    def keywords(text):
        words = re.findall(r'\b[a-zA-Z]{3,}\b', (text or '').lower())
        return {word for word in words if word not in stop_words}

    placeholders = ','.join('?' for _ in positive_reactions)

    liked_posts = query_db(
        f'''
        SELECT p.content
        FROM reactions r
        JOIN posts p ON p.id = r.post_id
        WHERE r.user_id = ?
          AND r.reaction_type IN ({placeholders})
        ''',
        (user_id, *positive_reactions)
    )

    if liked_posts is None:
        return []

    interest_words = set()

    for post in liked_posts:
        interest_words.update(keywords(post['content']))

    followed_users = query_db(
        '''
        SELECT followed_id
        FROM follows
        WHERE follower_id = ?
        ''',
        (user_id,)
    )

    if followed_users is None:
        return []

    followed_ids = {
        row['followed_id'] for row in followed_users
    }

    reacted_posts = query_db(
        '''
        SELECT post_id
        FROM reactions
        WHERE user_id = ?
        ''',
        (user_id,)
    )

    if reacted_posts is None:
        return []

    reacted_ids = {
        row['post_id'] for row in reacted_posts
    }

    candidates = query_db(
        '''
        SELECT p.id, p.content, p.created_at,
               u.username, u.id AS user_id,
               COUNT(r.id) AS total_reactions
        FROM posts p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN reactions r ON r.post_id = p.id
        WHERE p.user_id != ?
        GROUP BY p.id, p.content, p.created_at,
                 u.username, u.id
        ORDER BY p.created_at DESC
        ''',
        (user_id,)
    )

    if candidates is None:
        return []

    scored_posts = []

    for post in candidates:
        post_id = post['id']
        author_id = post['user_id']

        if post_id in reacted_ids:
            continue

        if filter_following and author_id not in followed_ids:
            continue

        post_keywords = keywords(post['content'])
        matching_words = len(interest_words & post_keywords)

        score = matching_words * 2

        if author_id in followed_ids:
            score += 3

        if not interest_words:
            score += 1

        scored_posts.append((
            score,
            str(post['created_at'] or ''),
            post['total_reactions'],
            post
        ))

    scored_posts.sort(
        key=lambda item: (item[0], item[1], item[2]),
        reverse=True
    )

    return [item[3] for item in scored_posts[:5]]


if __name__ == '__main__':
    app.run(debug=True, port=8080)

