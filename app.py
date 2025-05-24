from flask import Flask, render_template, request, redirect, session, url_for, flash
from database import Database, role_required
import psycopg2
from dotenv import load_dotenv
import os
import json
from flask import json as flask_json
import logging
from datetime import date
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')
app.config.update(
    DB_NAME=os.getenv('DB_NAME'),
    DB_USER=os.getenv('DB_USER'),
    DB_PASSWORD=os.getenv('DB_PASSWORD'),
    DB_HOST=os.getenv('DB_HOST')
)

db = Database(app)

def transliterate(text: str) -> str:
    conversion = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e',
        'ё': 'yo', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k',
        'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r',
        'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts',
        'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '',
        'э': 'e', 'ю': 'yu', 'я': 'ya'
    }
    
    # Оставляем латинские символы без изменений
    text = text.lower().replace(' ', '-')
    return ''.join(conversion.get(char, char) for char in text)

@app.route('/')
def index():
    return redirect(url_for('login' if 'user_id' not in session else 'profile'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            user_type = request.form['user_type']
            plain_password = request.form['password']
            
            user_id = db.execute(
                """INSERT INTO users (role_id, user_type, first_name, last_name, email, password)
                VALUES ((SELECT id FROM roles WHERE name = %s), %s, %s, %s, %s, %s) RETURNING id""",
                (user_type.capitalize(), user_type, request.form['first_name'], 
                 request.form['last_name'], request.form['email'], plain_password),
                fetch=True
            )[0][0]

            if user_type == 'mentor':
                db.execute("INSERT INTO mentors (id, bio) VALUES (%s, %s)", 
                          (user_id, request.form.get('bio', '')))
            elif user_type == 'teacher':
                db.execute("INSERT INTO teachers (id, qualifications) VALUES (%s, %s)",
                          (user_id, request.form.get('qualifications', '')))
            elif user_type == 'student':
                db.execute("INSERT INTO students (id) VALUES (%s)", (user_id,))

            flash('Регистрация прошла успешно!', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            flash(f'Ошибка регистрации: {str(e)}', 'danger')
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        try:
            user = db.execute(
                """SELECT u.id, u.password, u.user_type, r.name 
                FROM users u JOIN roles r ON u.role_id = r.id 
                WHERE email = %s""", 
                (email,), fetch=True
            )
            if user and user[0][1] == password:
                session.update({
                    'user_id': user[0][0],
                    'role': user[0][3],
                    'user_type': user[0][2]
                })
                return redirect(url_for('profile'))
            else:
                flash('Неверный email или пароль', 'danger')
        except Exception as e:
            flash('Ошибка входа', 'danger')
    return render_template('login.html')

@app.route('/profile')
@role_required('Student', 'Teacher', 'Mentor')
def profile():
    try:
        user_data = db.execute(
            "SELECT id, first_name, last_name, email, phone FROM users WHERE id = %s",
            (session['user_id'],), fetch=True
        )[0]
    except Exception as e:
        flash('Ошибка загрузки профиля', 'danger')
        return redirect(url_for('index'))
    return render_template('profile.html', user=user_data)

# Mentor: Courses
@app.route('/mentor/courses')
@role_required('Mentor')
def mentor_courses():
    try:
        courses = db.execute(
            """SELECT c.id, c.title, c.description, c.price, cc.name as category 
            FROM courses c
            JOIN course_categories cc ON c.category_id = cc.id
            WHERE c.mentor_id = %s""",
            (session['user_id'],), fetch=True
        )
        courses_list = [{'id': c[0], 'title': c[1], 'description': c[2], 'price': c[3], 'category': c[4]} for c in courses]
    except Exception:
        flash('Ошибка загрузки курсов', 'danger')
        return redirect(url_for('index'))
    return render_template('mentor/courses.html', courses=courses_list)

@app.route('/mentor/course/create', methods=['GET', 'POST'])
@role_required('Mentor')
def create_course():
    categories = db.execute("SELECT id, name FROM course_categories ORDER BY name", fetch=True)
    if not categories:
        flash('Для создания курса сначала добавьте категорию', 'danger')
        return redirect(url_for('manage_categories'))
    if request.method == 'POST':
        try:
            title = request.form['title'].strip()
            description = request.form['description'].strip()
            price = float(request.form['price'])
            category_id = int(request.form['category'])
            if not title or not description:
                raise ValueError("Заполните все обязательные поля")
            db.execute(
                """INSERT INTO courses (title, description, price, mentor_id, category_id)
                VALUES (%s, %s, %s, %s, %s)""",
                (title, description, price, session['user_id'], category_id)
            )
            flash('Курс успешно создан', 'success')
            return redirect(url_for('mentor_courses'))
        except ValueError as e:
            flash(f'Ошибка в данных: {str(e)}', 'danger')
        except Exception as e:
            flash(f'Ошибка создания курса: {str(e)}', 'danger')
    return render_template('mentor/create_course.html', categories=categories)

@app.route('/mentor/course/<int:course_id>')
@role_required('Mentor')
def course_detail(course_id):
    try:
        # Проверяем права и получаем данные курса
        course_data = db.execute(
            """
            SELECT c.id, c.title, c.description, c.price, cc.name
            FROM courses c
            JOIN course_categories cc ON c.category_id = cc.id
            WHERE c.id = %s AND c.mentor_id = %s
            """,
            (course_id, session['user_id']), fetch=True
        )
        if not course_data:
            flash('Курс не найден или доступ запрещён', 'danger')
            return redirect(url_for('mentor_courses'))
        course = course_data[0]

        lessons = db.execute(
            """
            SELECT
              l.id,
              l.title,
              l.content,
              l.video_url,
              l.position,
              COALESCE((
                SELECT json_agg(json_build_object(
                  'id', r.id,
                  'title', r.title,
                  'file_url', r.file_url,
                  'file_type', r.file_type
                ))
                FROM lesson_resources r
                WHERE r.lesson_id = l.id
              ), '[]'::json) AS resources,
              COALESCE((
                SELECT json_agg(json_build_object(
                  'id', a.id,
                  'title', a.title,
                  'description', a.description,
                  'max_score', a.max_score
                ))
                FROM assignments a
                WHERE a.lesson_id = l.id
              ), '[]'::json) AS assignments
            FROM lessons l
            WHERE l.course_id = %s
            ORDER BY l.position
            """,
            (course_id,), fetch=True
        )

        processed = []
        for lsn in lessons:
            processed.append({
                'id':         lsn[0],
                'title':      lsn[1],
                'content':    lsn[2] or "Описание отсутствует",
                'video_url':  lsn[3] or "#",
                'position':   lsn[4],
                'resources':  lsn[5] if isinstance(lsn[5], list) else [],
                'assignments': lsn[6] if isinstance(lsn[6], list) else []
            })

        course_dict = {
            'id':          course[0],
            'title':       course[1],
            'description': course[2],
            'price':       float(course[3]),
            'category':    course[4]
        }

        return render_template(
            'mentor/course_detail.html',
            course=course_dict,
            lessons=processed
        )

    except Exception as e:
        flash(f'Ошибка загрузки деталей курса: {e}', 'danger')
        return redirect(url_for('mentor_courses'))

@app.route('/move_lesson/<direction>/<int:lesson_id>', methods=['POST'])
@role_required('Mentor')
def move_lesson_position(direction, lesson_id):
    try:
        # Получаем текущий урок
        current_lesson = db.execute(
            "SELECT id, position, course_id FROM lessons WHERE id = %s",
            (lesson_id,),
            fetch=True
        )[0]
        
        course_id = current_lesson[2]
        current_pos = current_lesson[1]

        # Определяем целевой урок для обмена позициями
        if direction == 'up':
            target_pos = current_pos - 1
        elif direction == 'down':
            target_pos = current_pos + 1
        else:
            return redirect(url_for('course_detail', course_id=course_id))

        # Находим урок для обмена
        target_lesson = db.execute(
            "SELECT id FROM lessons WHERE course_id = %s AND position = %s",
            (course_id, target_pos),
            fetch=True
        )

        if target_lesson:
            # Меняем позиции местами
            db.execute(
                "UPDATE lessons SET position = %s WHERE id = %s",
                (current_pos, target_lesson[0][0])
            )
            db.execute(
                "UPDATE lessons SET position = %s WHERE id = %s",
                (target_pos, lesson_id)
            )

        flash('Позиция урока изменена', 'success')
    except Exception as e:
        flash(f'Ошибка: {str(e)}', 'danger')
    
    return redirect(url_for('course_detail', course_id=course_id))

@app.route('/mentor/course/<int:course_id>/lesson/create', methods=['POST'])
@role_required('Mentor')
def create_lesson(course_id):
    try:
        title = request.form['title']
        content = request.form['content']
        video_url = request.form['video_url']
        
        position = db.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM lessons WHERE course_id = %s",
            (course_id,),
            fetch=True
        )[0][0]

        db.execute(
            """INSERT INTO lessons (title, content, video_url, course_id, position)
            VALUES (%s, %s, %s, %s, %s)""",
            (title, content, video_url, course_id, position)
        )
        flash('Урок успешно создан', 'success')
        
    except Exception as e:
        flash(f'Ошибка: {str(e)}', 'danger')
    
    return redirect(url_for('course_detail', course_id=course_id))
@app.route('/mentor/lesson/update_order', methods=['POST'])
@role_required('Mentor')
def update_lesson_order():
    try:
        lesson_ids = request.json.get('order', [])
        for index, lesson_id in enumerate(lesson_ids, 1):
            db.execute(
                "UPDATE lessons SET position = %s WHERE id = %s",
                (index, lesson_id))
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/mentor/lesson/delete/<int:lesson_id>', methods=['POST']) 
@role_required('Mentor')
def delete_lesson(lesson_id):
    try:
        # Проверка прав
        course_id = db.execute(
            """SELECT c.id 
            FROM courses c
            JOIN lessons l ON c.id = l.course_id 
            WHERE l.id = %s AND c.mentor_id = %s""",
            (lesson_id, session['user_id']),
            fetch=True
        )
        
        if not course_id:
            flash('Урок не найден или нет прав', 'danger')
            return redirect(url_for('mentor_courses'))
            
        db.execute("DELETE FROM lessons WHERE id = %s", (lesson_id,))
        flash('Урок успешно удален', 'success')
    except Exception as e:
        flash(f'Ошибка удаления: {str(e)}', 'danger')
    return redirect(url_for('course_detail', course_id=course_id[0][0]))

@app.route('/mentor/lesson/edit/<int:lesson_id>', methods=['GET', 'POST'])
@role_required('Mentor')
def edit_lesson(lesson_id):
    try:
        # Проверка прав
        lesson_data = db.execute(
            """SELECT l.id, l.title, l.content, l.video_url, l.course_id 
            FROM lessons l
            JOIN courses c ON l.course_id = c.id
            WHERE l.id = %s AND c.mentor_id = %s""",
            (lesson_id, session['user_id']),
            fetch=True
        )[0]

        lesson = {
            'id': lesson_data[0],
            'title': lesson_data[1],
            'content': lesson_data[2],
            'video_url': lesson_data[3], 
            'course_id': lesson_data[4]
        }

        if request.method == 'POST':
            db.execute(
                "UPDATE lessons SET title = %s, content = %s, video_url = %s WHERE id = %s",
                (request.form['title'],
                 request.form['content'],
                 request.form['video_url'],
                 lesson_id)
            )
            flash('Урок обновлен', 'success')
            return redirect(url_for('course_detail', course_id=lesson['course_id']))

    except IndexError:
        flash('Урок не найден', 'danger')
        return redirect(url_for('mentor_courses'))
    except Exception as e:
        flash(f'Ошибка: {str(e)}', 'danger')
        return redirect(url_for('mentor_courses'))

    return render_template('mentor/edit_lesson.html', lesson=lesson)
@app.route('/mentor/course/edit/<int:course_id>', methods=['GET', 'POST'])
@role_required('Mentor')
def edit_course(course_id):
    try:
        if request.method == 'POST':
            # Обновление данных курса
            db.execute(
                """UPDATE courses SET
                title = %s, 
                description = %s, 
                price = %s, 
                category_id = %s
                WHERE id = %s AND mentor_id = %s""",
                (request.form['title'],
                 request.form['description'],
                 request.form['price'],
                 request.form['category'],
                 course_id,
                 session['user_id'])
            )
            flash('Курс успешно обновлен', 'success')
            return redirect(url_for('mentor_courses'))

         # Получаем курс и категории
        course_data = db.execute(
            """SELECT c.id, c.title, c.description, c.price, c.category_id 
            FROM courses c
            WHERE c.id = %s AND c.mentor_id = %s""",
            (course_id, session['user_id']),
            fetch=True
        )[0]

        categories = db.execute(
            "SELECT id, name FROM course_categories ORDER BY name",
            fetch=True
        )

        return render_template(
            'mentor/edit_course.html',
            course={
                'id': course_data[0],
                'title': course_data[1],
                'description': course_data[2],
                'price': course_data[3],
                'category_id': course_data[4]
            },
            categories=[dict(id=c[0], name=c[1]) for c in categories]  # Преобразуем в словари
        )

    except IndexError:
        flash('Курс не найден', 'danger')
        return redirect(url_for('mentor_courses'))
    except Exception as e:
        flash(f'Ошибка: {str(e)}', 'danger')
        return redirect(url_for('mentor_courses'))
@app.route('/mentor/course/delete/<int:course_id>', methods=['POST'])
@role_required('Mentor')
def delete_course(course_id):
    try:
        db.execute(
            "DELETE FROM courses WHERE id = %s AND mentor_id = %s",
            (course_id, session['user_id'])
        )
        flash('Курс успешно удален', 'success')
    except Exception as e:
        flash(f'Ошибка удаления: {str(e)}', 'danger')
    return redirect(url_for('mentor_courses'))

# Ментор: Управление категориями
@app.route('/mentor/categories', methods=['GET', 'POST'])
@role_required('Mentor')
def manage_categories():
    try:
        if request.method == 'POST':
            category_name = request.form['category_name']
            slug = transliterate(category_name)
            db.execute(
                "INSERT INTO course_categories (name, slug) VALUES (%s, %s)",
                (category_name, slug)
            )
            flash('Категория создана', 'success')
        
        # Преобразуем кортежи в словари
        categories = db.execute(
            "SELECT id, name, slug FROM course_categories ORDER BY name",
            fetch=True
        )
        categories = [{'id': c[0], 'name': c[1], 'slug': c[2]} for c in categories]
        
    except Exception as e:
        flash(f'Ошибка: {str(e)}', 'danger')
        categories = []
    
    return render_template('mentor/categories.html', categories=categories)

@app.route('/mentor/category/edit/<int:category_id>', methods=['GET', 'POST'])
@role_required('Mentor')
def edit_category(category_id):
    if request.method == 'POST':
        try:
            new_name = request.form['name']
            slug = transliterate(new_name)
            db.execute(
                "UPDATE course_categories SET name = %s, slug = %s WHERE id = %s",
                (new_name, slug, category_id)
            )
            flash('Категория обновлена', 'success')
            return redirect(url_for('manage_categories'))
        except Exception as e:
            flash(f'Ошибка: {str(e)}', 'danger')
    
    try:
        category = db.execute(
            "SELECT id, name FROM course_categories WHERE id = %s",
            (category_id,), fetch=True
        )[0]
    except IndexError:
        flash('Категория не найдена', 'danger')
        return redirect(url_for('manage_categories'))
    return render_template('mentor/edit_category.html', category=category)

@app.route('/mentor/category/delete/<int:category_id>')
@role_required('Mentor')
def delete_category(category_id):
    try:
        db.execute(
            "DELETE FROM course_categories WHERE id = %s",
            (category_id,)
        )
        flash('Категория удалена', 'success')
    except Exception as e:
        flash(f'Ошибка: {str(e)}', 'danger')
    return redirect(url_for('manage_categories'))

# Просмотр и управление заданиями
@app.route('/mentor/lesson/<int:lesson_id>/assignments')
@role_required('Mentor')
def lesson_assignments(lesson_id):
    try:
        # Получаем урок и проверяем права
        lesson = db.execute(
            """
            SELECT l.id, l.title, c.id AS course_id
            FROM lessons l
            JOIN courses c ON l.course_id = c.id
            WHERE l.id = %s AND c.mentor_id = %s
            """,
            (lesson_id, session['user_id']), fetch=True
        )[0]
        # Получаем сами задания
        assigns = db.execute(
            """
            SELECT id, title, description, max_score
            FROM assignments
            WHERE lesson_id = %s
            ORDER BY id
            """,
            (lesson_id,), fetch=True
        )
        # Формируем данные для шаблона
        assignments = [
            {
                'id': a[0],
                'title': a[1],
                'description': a[2],
                'max_score': a[3]
            } for a in assigns
        ]
        return render_template(
            'mentor/assignments.html',
            lesson={'id': lesson[0], 'title': lesson[1], 'course_id': lesson[2]},
            assignments=assignments
        )
    except Exception as e:
        flash(f'Ошибка: {e}', 'danger')
        return redirect(url_for('mentor_courses'))

@app.route('/mentor/assignment/add/<int:lesson_id>', methods=['GET', 'POST'])
@role_required('Mentor')
def add_assignment(lesson_id):
    # Проверяем, что урок принадлежит текущему ментору
    data = db.execute(
        """
        SELECT l.id, l.title, l.course_id
        FROM lessons l
        JOIN courses c ON l.course_id = c.id
        WHERE l.id = %s AND c.mentor_id = %s
        """,
        (lesson_id, session['user_id']), fetch=True
    )
    if not data:
        flash('Урок не найден или доступ запрещён', 'danger')
        return redirect(url_for('mentor_courses'))
    lesson = {'id': data[0][0], 'title': data[0][1], 'course_id': data[0][2]}

    if request.method == 'POST':
        try:
            # Вставляем задание
            db.execute(
                """
                INSERT INTO assignments (title, description, max_score, lesson_id)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    request.form['title'],
                    request.form['description'],
                    request.form.get('max_score') or None,
                    lesson_id
                )
            )

            flash('Задание успешно создано', 'success')
            return redirect(url_for('course_detail', course_id=lesson['course_id']))
        except Exception as e:
            app.logger.error(f"Ошибка при добавлении задания: {e}")
            flash(f'Ошибка создания задания: {e}', 'danger')

    return render_template('mentor/add_assignment.html', lesson=lesson)



@app.route('/mentor/assignment/<int:assignment_id>', methods=['GET', 'POST'])
@role_required('Mentor')
def view_assignment(assignment_id):
    # Получаем задание и проверяем права
    data = db.execute(
        """
        SELECT a.id, a.title, a.description, a.max_score, a.lesson_id
        FROM assignments a
        JOIN lessons l ON a.lesson_id = l.id
        JOIN courses c ON l.course_id = c.id
        WHERE a.id = %s AND c.mentor_id = %s
        """,
        (assignment_id, session['user_id']), fetch=True
    )
    if not data:
        flash('Задание не найдено или доступ запрещён', 'danger')
        return redirect(url_for('mentor_courses'))

    a = data[0]
    assignment = {
        'id':           a[0],
        'title':        a[1],
        'description':  a[2],
        'max_score':    a[3],
        'lesson_id':    a[4]
    }

    if request.method == 'POST':
        try:
            db.execute(
                """
                UPDATE assignments
                   SET title = %s,
                       description = %s,
                       max_score = %s
                 WHERE id = %s
                """,
                (
                    request.form['title'],
                    request.form['description'],
                    request.form.get('max_score') or None,
                    assignment_id
                )
            )
            flash('Задание обновлено', 'success')
            return redirect(url_for('view_assignment', assignment_id=assignment_id))
        except Exception as e:
            flash(f'Ошибка обновления: {e}', 'danger')

    return render_template('mentor/view_assignment.html', assignment=assignment)

@app.route('/mentor/assignment/delete/<int:assignment_id>', methods=['POST'])
@role_required('Mentor')
def delete_assignment(assignment_id):
    try:
        # Выясняем lesson_id для редиректа
        lesson_id = db.execute(
            "SELECT lesson_id FROM assignments WHERE id = %s",
            (assignment_id,), fetch=True
        )[0][0]
        db.execute("DELETE FROM assignments WHERE id = %s", (assignment_id,))
        flash('Задание удалено', 'success')
    except Exception as e:
        flash(f'Ошибка удаления: {e}', 'danger')
        return redirect(url_for('mentor_courses'))

    return redirect(url_for('lesson_assignments', lesson_id=lesson_id))

# Добавление ресурса
@app.route('/mentor/lesson/<int:lesson_id>/resource/add', methods=['GET', 'POST'])
@role_required('Mentor')
def add_resource(lesson_id):
    try:
        if request.method == 'POST':
            db.execute(
                """INSERT INTO lesson_resources 
                (title, file_url, file_type, lesson_id)
                VALUES (%s, %s, %s, %s)""",
                (request.form['title'],
                 request.form['file_url'],
                 request.form['file_type'],
                 lesson_id)
            )
            flash('Ресурс добавлен', 'success')
            return redirect(url_for('course_detail', course_id=request.form['course_id']))

        # Получаем информацию о курсе
        course_data = db.execute(
            """SELECT c.id 
            FROM courses c
            JOIN lessons l ON c.id = l.course_id 
            WHERE l.id = %s""",
            (lesson_id,),
            fetch=True
        )[0]

        lesson = db.execute(
            "SELECT id, title FROM lessons WHERE id = %s",
            (lesson_id,),
            fetch=True
        )[0]
        
        return render_template(
            'mentor/add_resource.html',
            lesson={'id': lesson[0], 'title': lesson[1]},
            course={'id': course_data[0]}
        )
        
    except Exception as e:
        flash(f'Ошибка: {str(e)}', 'danger')
        return redirect(url_for('mentor_courses'))

# Редактирование ресурса
@app.route('/mentor/resource/<int:resource_id>', methods=['GET', 'POST'])
@role_required('Mentor')
def edit_resource(resource_id):
    try:
        resource = db.execute(
            """SELECT r.*, c.id as course_id 
            FROM lesson_resources r
            JOIN lessons l ON r.lesson_id = l.id
            JOIN courses c ON l.course_id = c.id
            WHERE r.id = %s AND c.mentor_id = %s""",
            (resource_id, session['user_id']),
            fetch=True
        )[0]

        if request.method == 'POST':
            db.execute(
                """UPDATE lesson_resources SET
                title = %s,
                file_url = %s,
                file_type = %s
                WHERE id = %s""",
                (request.form['title'],
                 request.form['file_url'],
                 request.form['file_type'],
                 resource_id)
            )
            flash('Ресурс обновлен', 'success')
            return redirect(url_for('course_detail', course_id=resource[4]))

        return render_template(
            'mentor/edit_resource.html',
            resource={
                'id': resource[0],
                'title': resource[1],
                'file_url': resource[2],
                'file_type': resource[3],
                'lesson_id': resource[4]
            }
        )
        
    except IndexError:
        flash('Ресурс не найден', 'danger')
        return redirect(url_for('mentor_courses'))
    except Exception as e:
        flash(f'Ошибка: {str(e)}', 'danger')
        return redirect(url_for('mentor_courses'))

# Удаление ресурса
@app.route('/mentor/resource/delete/<int:resource_id>', methods=['POST'])
@role_required('Mentor')
def delete_resource(resource_id):
    try:
        lesson_id = db.execute(
            "SELECT lesson_id FROM lesson_resources WHERE id = %s",
            (resource_id,),
            fetch=True
        )[0][0]
        
        db.execute(
            "DELETE FROM lesson_resources WHERE id = %s",
            (resource_id,)
        )
        flash('Ресурс удален', 'success')
    except Exception as e:
        flash(f'Ошибка удаления: {str(e)}', 'danger')
    return redirect(url_for('course_detail', course_id=request.form['course_id']))
#Преподаватель
@app.route('/teacher/groups')
@role_required('Teacher')
def teacher_groups():
    try:
        groups = db.execute(
            """SELECT g.id, g.name, c.title AS course_title, g.start_date, g.max_students
            FROM groups g
            JOIN courses c ON g.course_id = c.id
            WHERE g.teacher_id = %s""",
            (session['user_id'],), fetch=True
        )
        groups_list = [{'id': g[0], 'name': g[1], 'course_title': g[2], 'start_date': g[3], 'max_students': g[4]} for g in groups]
    except Exception as e:
        flash(f'Ошибка загрузки групп: {str(e)}', 'danger')
        return redirect(url_for('index'))
    return render_template('teacher/groups.html', groups=groups_list)



@app.route('/teacher/group/create', methods=['GET', 'POST'])
@role_required('Teacher')
def create_group():
    # берём все курсы из БД
    raw = db.execute("SELECT id, title FROM courses ORDER BY title", fetch=True)
    courses = [{'id': r[0], 'title': r[1]} for r in raw]

    if request.method == 'POST':
        try:
            name = request.form['name'].strip()
            course_id = int(request.form['course_id'])
            start_date = request.form['start_date']
            max_students = int(request.form['max_students'])
            db.execute(
                """INSERT INTO groups (name, course_id, teacher_id, start_date, max_students)
                   VALUES (%s, %s, %s, %s, %s)""",
                (name, course_id, session['user_id'], start_date, max_students)
            )
            flash('Группа успешно создана', 'success')
            return redirect(url_for('teacher_groups'))
        except Exception as e:
            flash(f'Ошибка создания группы: {e}', 'danger')

    return render_template('teacher/create_group.html', courses=courses)


# Редактирование группы
@app.route('/teacher/group/edit/<int:group_id>', methods=['GET', 'POST'])
@role_required('Teacher')
def edit_group(group_id):
    # все курсы
    raw = db.execute("SELECT id, title FROM courses ORDER BY title", fetch=True)
    courses = [{'id': r[0], 'title': r[1]} for r in raw]

    # данные группы
    group_data = db.execute(
        """SELECT id, name, course_id, start_date, max_students
           FROM groups
           WHERE id = %s AND teacher_id = %s""",
        (group_id, session['user_id']), fetch=True
    )
    if not group_data:
        flash('Группа не найдена или доступ запрещён', 'danger')
        return redirect(url_for('teacher_groups'))

    g = group_data[0]
    group = {
        'id':           g[0],
        'name':         g[1],
        'course_id':    g[2],
        'start_date':   g[3].isoformat(),
        'max_students': g[4]
    }

    if request.method == 'POST':
        try:
            db.execute(
                """UPDATE groups
                   SET name = %s,
                       course_id = %s,
                       start_date = %s,
                       max_students = %s
                   WHERE id = %s""",
                (request.form['name'].strip(),
                 int(request.form['course_id']),
                 request.form['start_date'],
                 int(request.form['max_students']),
                 group_id)
            )
            flash('Группа обновлена', 'success')
            return redirect(url_for('teacher_groups'))
        except Exception as e:
            flash(f'Ошибка обновления группы: {e}', 'danger')

    return render_template('teacher/edit_group.html', group=group, courses=courses)

@app.route('/teacher/group/delete/<int:group_id>', methods=['POST'])
@role_required('Teacher')
def delete_group(group_id):
    try:
        db.execute("DELETE FROM groups WHERE id = %s AND teacher_id = %s", (group_id, session['user_id']))
        flash('Группа удалена', 'success')
    except Exception as e:
        flash(f'Ошибка удаления группы: {str(e)}', 'danger')
    return redirect(url_for('teacher_groups'))

from datetime import date

@app.route('/student/courses', methods=['GET'])
@role_required('Student')
def student_courses():
    student_id = session['user_id']

    # Параметры поиска/фильтрации
    q = request.args.get('q', '').strip()
    category = request.args.get('category', type=int)
    min_price = request.args.get('min_price', type=float)
    max_price = request.args.get('max_price', type=float)

    # Список категорий для фильтрации
    cats = db.execute("SELECT id, name FROM course_categories ORDER BY name", fetch=True)
    categories = [{'id': c[0], 'name': c[1]} for c in cats]

    where = []
    params = []
    if q:
        where.append("(c.title ILIKE %s OR c.description ILIKE %s)")
        term = f"%{q}%"
        params += [term, term]
    if category:
        where.append("c.category_id = %s")
        params.append(category)
    if min_price is not None:
        where.append("c.price >= %s")
        params.append(min_price)
    if max_price is not None:
        where.append("c.price <= %s")
        params.append(max_price)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    # Получаем курсы
    courses_raw = db.execute(
        f"""SELECT c.id, c.title, c.description, c.price, cc.id, cc.name
            FROM courses c
            JOIN course_categories cc ON c.category_id = cc.id
            {where_sql}
            ORDER BY c.title""",
        tuple(params), fetch=True
    )

    # Получаем ID курсов, на которые студент уже записан
    enrolled_ids = db.execute(
        """
        SELECT DISTINCT c.id
        FROM enrollments e
        JOIN groups g ON e.group_id = g.id
        JOIN courses c ON g.course_id = c.id
        WHERE e.student_id = %s
        """, (student_id,), fetch=True
    )
    enrolled_course_ids = {row[0] for row in enrolled_ids}

    courses = []
    for cid, title, desc, price, cat_id, cat_name in courses_raw:
        # Поиск ближайшей группы с местами
        grp = db.execute(
            """
            SELECT g.id, COUNT(e.student_id) AS cnt
            FROM groups g
            LEFT JOIN enrollments e ON g.id = e.group_id
            WHERE g.course_id = %s
              AND g.start_date > %s
            GROUP BY g.id, g.max_students
            HAVING COUNT(e.student_id) < g.max_students
            ORDER BY cnt ASC, g.start_date ASC
            LIMIT 1
            """,
            (cid, date.today()), fetch=True
        )
        group_id = grp[0][0] if grp else None

        courses.append({
            'id': cid,
            'title': title,
            'description': desc,
            'price': price,
            'category_id': cat_id,
            'category_name': cat_name,
            'available_group_id': group_id,
            'already_enrolled': cid in enrolled_course_ids
        })

    return render_template(
        'student/courses.html',
        courses=courses,
        categories=categories,
        filters={'q': q, 'category': category, 'min_price': min_price, 'max_price': max_price}
    )



@app.route('/student/course/join/<int:course_id>', methods=['POST'])
@role_required('Student')
def join_course(course_id):
    # находим подходящую группу
    grp = db.execute(
        """
        SELECT g.id, COUNT(e.student_id) AS cnt, g.max_students, g.start_date, c.price
        FROM groups g
        LEFT JOIN enrollments e ON g.id = e.group_id
        JOIN courses c ON g.course_id = c.id
        WHERE g.course_id = %s
          AND g.start_date > %s
        GROUP BY g.id, g.max_students, g.start_date, c.price
        HAVING COUNT(e.student_id) < g.max_students
        ORDER BY cnt ASC, g.start_date ASC
        LIMIT 1
        """,
        (course_id, date.today()), fetch=True
    )
    if not grp:
        flash('К сожалению, в этом курсе нет доступных групп.', 'warning')
        return redirect(url_for('student_courses'))

    group_id, cnt, max_students, start_date, price = grp[0]
    return redirect(url_for('pay_course', group_id=group_id))


# форма оплаты
@app.route('/student/course/pay/<int:group_id>', methods=['GET', 'POST'])
@role_required('Student')
def pay_course(group_id):
    # Достаем данные группы и курса
    data = db.execute(
        """
        SELECT g.id, g.start_date, c.id AS course_id, c.title, c.price
        FROM groups g
        JOIN courses c ON g.course_id = c.id
        WHERE g.id = %s
        """,
        (group_id,), fetch=True
    )
    if not data:
        flash('Группа не найдена', 'danger')
        return redirect(url_for('student_courses'))

    g_id, start_date, course_id, course_title, amount = data[0]

    # Проверяем, не записан ли студент уже в эту группу
    existing_enrollment = db.execute(
        """
        SELECT 1 FROM enrollments WHERE student_id = %s AND group_id = %s
        """,
        (session['user_id'], g_id), fetch=True
    )

    if existing_enrollment:
        flash('Вы уже записаны в эту группу.', 'warning')
        return redirect(url_for('student_courses'))

    if request.method == 'POST':
        try:
            db.execute(
                """INSERT INTO enrollments (student_id, group_id, enrollment_date, status)
                   VALUES (%s, %s, %s, %s)""",
                (session['user_id'], g_id, date.today(), 'active')
            )

            db.execute(
                """INSERT INTO payments (student_id, group_id, amount, status)
                   VALUES (%s, %s, %s, %s)""",
                (session['user_id'], g_id, amount, 'paid')
            )


            flash('Оплата прошла успешно! Вы записаны на курс.', 'success')
            return redirect(url_for('student_courses'))
        except Exception as e:
            flash(f'Ошибка при оплате: {e}', 'danger')

    return render_template(
        'student/payment.html',
        group={'id': g_id, 'start_date': start_date},
        course={'id': course_id, 'title': course_title},
        amount=amount
    )


@app.route('/teacher/group/<int:group_id>', methods=['GET'])
@role_required('Teacher')
def teacher_group_detail(group_id):
    group_data = db.execute("""
        SELECT g.id, g.name, c.title
        FROM groups g
        JOIN courses c ON g.course_id = c.id
        WHERE g.id = %s
    """, (group_id,), fetch=True)

    if not group_data:
        flash("Группа не найдена", "danger")
        return redirect(url_for('teacher_groups'))

    group_id, group_name, course_title = group_data[0]

    # Получаем студентов + их домашние задания
    students_raw = db.execute("""
        SELECT u.id, u.first_name || ' ' || u.last_name AS full_name, u.email,
               a.title, s.submission_text, s.submission_link, s.status
        FROM enrollments e
        JOIN users u ON u.id = e.student_id
        LEFT JOIN assignment_statuses s ON s.student_id = u.id AND s.group_id = e.group_id
        LEFT JOIN assignments a ON a.id = s.assignment_id
        WHERE e.group_id = %s
        ORDER BY full_name, a.title
    """, (group_id,), fetch=True)

    # Группируем задания по студентам
    students = {}
    for s_id, name, email, a_title, a_text, a_link, status in students_raw:
        if s_id not in students:
            students[s_id] = {
                'id': s_id,
                'name': name,
                'email': email,
                'assignments': []
            }
        if a_title:
            students[s_id]['assignments'].append({
                'title': a_title,
                'text': a_text,
                'link': a_link,
                'status': status
            })

    return render_template(
        'teacher/group_detail.html',
        group={'id': group_id, 'name': group_name, 'course_title': course_title},
        students=list(students.values())
    )


@app.route('/teacher/group/<int:group_id>/student/<int:student_id>/assignments', methods=['GET'])
@role_required('Teacher')
def review_student_assignments(group_id, student_id):
    assignments = db.execute("""
        SELECT a.id, a.title, s.submission_text, s.submission_link, s.status, s.teacher_comment, a.max_score, s.grade
        FROM assignment_statuses s
        JOIN assignments a ON a.id = s.assignment_id
        WHERE s.student_id = %s AND s.group_id = %s
    """, (student_id, group_id), fetch=True)

    return render_template(
        'teacher/student_assignments.html',
        assignments=[
            {
                'assignment_id': a[0],
                'title': a[1],
                'submission_text': a[2],
                'submission_link': a[3],
                'status': a[4],
                'teacher_comment': a[5],
                'max_score': a[6],
                'grade': a[7] if len(a) > 7 else None
            }
            for a in assignments
        ],
        group_id=group_id,
        student_id=student_id
    )




@app.route('/teacher/review/<int:assignment_id>/student/<int:student_id>', methods=['GET', 'POST'])
@role_required('Teacher')
def review_student_assignment(assignment_id, student_id):
    status_data = db.execute("""
        SELECT s.group_id, a.title, s.submission_text, s.submission_link, s.status, s.teacher_comment, s.grade, a.max_score
        FROM assignment_statuses s
        JOIN assignments a ON a.id = s.assignment_id
        WHERE s.assignment_id = %s AND s.student_id = %s
    """, (assignment_id, student_id), fetch=True)

    if not status_data:
        flash("Задание не найдено", "danger")
        return redirect(url_for('teacher_groups'))

    group_id, title, text, link, status, teacher_comment, grade, max_score = status_data[0]

    if request.method == 'POST':
        new_review = request.form.get('review')
        new_grade = request.form.get('grade')

        new_status = 'completed'  # Статус после завершения ревью

        db.execute("""
            UPDATE assignment_statuses
            SET teacher_comment = %s, grade = %s, status = %s
            WHERE assignment_id = %s AND student_id = %s
        """, (new_review, new_grade, new_status, assignment_id, student_id))

        flash("Ревью сохранено", "success")
        return redirect(url_for('review_student_assignments', group_id=group_id, student_id=student_id))

    return render_template('teacher/review_student_assignment.html', assignment={
        'id': assignment_id,
        'student_id': student_id,
        'title': title,
        'text': text,
        'link': link,
        'status': status,
        'teacher_comment': teacher_comment,
        'grade': grade,
        'max_score': max_score
    })





# Список приобретённых курсов
@app.route('/student/my-courses')
@role_required('Student')
def student_my_courses():
    student_id = session['user_id']
    raw = db.execute("""
        SELECT DISTINCT c.id, c.title, c.description, c.price
        FROM courses c
        JOIN groups g ON g.course_id = c.id
        JOIN enrollments e ON e.group_id = g.id
        WHERE e.student_id = %s
    """, (student_id,), fetch=True)

    courses = [{'id': r[0], 'title': r[1], 'description': r[2], 'price': r[3]} for r in raw]
    return render_template('student/my_courses.html', courses=courses)


@app.route('/student/my-course/<int:course_id>')
@role_required('Student')
def student_course_detail(course_id):
    student_id = session['user_id']

    # Проверка доступа
    authorized = db.execute("""
        SELECT 1
        FROM courses c
        JOIN groups g ON g.course_id = c.id
        JOIN enrollments e ON e.group_id = g.id
        WHERE e.student_id = %s AND c.id = %s
    """, (student_id, course_id), fetch=True)

    if not authorized:
        flash("У вас нет доступа к этому курсу", "danger")
        return redirect(url_for('student_my_courses'))

    # Получаем информацию о курсе
    course_row = db.execute(
        "SELECT title, description FROM courses WHERE id = %s", (course_id,), fetch=True
    )
    course = {'id': course_id, 'title': course_row[0][0], 'description': course_row[0][1]}

    # Получаем уроки
    lessons_raw = db.execute("""
        SELECT id, title, content, video_url
        FROM lessons
        WHERE course_id = %s
        ORDER BY position
    """, (course_id,), fetch=True)

    lessons = []
    all_completed = True  # Флаг для проверки, все ли задания завершены
    total_score = 0  # Общая оценка студента
    total_max_score = 0  # Максимальная возможная оценка для вычисления процента

    for l in lessons_raw:
        lesson_id = l[0]

        # Ресурсы
        resources = db.execute("""
            SELECT title, file_url, file_type
            FROM lesson_resources
            WHERE lesson_id = %s
        """, (lesson_id,), fetch=True)

        # Задания
        assignments_raw = db.execute("""
            SELECT a.id, a.title, a.description, a.max_score,
                   s.status, s.submission_text, s.submission_link, s.teacher_comment, s.grade
            FROM assignments a
            LEFT JOIN assignment_statuses s 
                ON a.id = s.assignment_id AND s.student_id = %s
            WHERE a.lesson_id = %s
        """, (student_id, lesson_id), fetch=True)

        assignments = [{
            'id': a[0],
            'title': a[1],
            'description': a[2],
            'max_score': a[3],
            'status': a[4],
            'submission_text': a[5],
            'submission_link': a[6],
            'teacher_comment': a[7],
            'grade': a[8]
        } for a in assignments_raw]

        # Проверка на завершение всех заданий
        for assignment in assignments:
            if assignment['status'] != 'completed':
                all_completed = False  # Если хотя бы одно задание не завершено
            if assignment['grade'] is not None:
                total_score += assignment['grade']  # Добавляем оценку за задание
            total_max_score += assignment['max_score']  # Добавляем максимальный балл

        lessons.append({
            'id': lesson_id,
            'title': l[1],
            'content': l[2],
            'video_url': l[3],
            'resources': [{'title': r[0], 'url': r[1], 'type': r[2]} for r in resources],
            'assignments': assignments
        })

    # Итоговая оценка в процентах
    final_grade = None
    if total_max_score > 0:
        final_grade = (total_score / total_max_score) * 100

    # Переводим итоговую оценку в шкалу от 0 до 5
    final_grade_5_scale = None
    if final_grade is not None:
        final_grade_5_scale = (final_grade / 100) * 5

    # Записываем в таблицу certificates, если все задания завершены и записи еще нет
    if all_completed and final_grade is not None:
        try:
            # Проверяем, существует ли уже запись в таблице certificates
            existing_certificate = db.execute("""
                SELECT 1
                FROM certificates
                WHERE student_id = %s AND group_id IN (
                    SELECT e.group_id
                    FROM enrollments e
                    JOIN groups g ON e.group_id = g.id
                    WHERE g.course_id = %s
                )
            """, (student_id, course_id), fetch=True)

            # Если записи нет, добавляем новую
            if not existing_certificate:
                db.execute("""
                    INSERT INTO certificates (student_id, group_id, issued_at, grade)
                    SELECT %s, e.group_id, NOW(), %s
                    FROM enrollments e
                    WHERE e.student_id = %s AND e.group_id IN (
                        SELECT g.id
                        FROM groups g
                        WHERE g.course_id = %s
                    )
                """, (student_id, final_grade_5_scale, student_id, course_id))
            else:
                app.logger.info(f"Запись сертификата для студента {student_id} и курса {course_id} уже существует.")
        except Exception as e:
            app.logger.error(f"Ошибка при добавлении сертификата: {e}")
            flash(f'Ошибка при добавлении сертификата: {e}', 'danger')

        # Обновляем статус в enrollments на "completed"
        try:
            db.execute("""
                UPDATE enrollments
                SET status = 'completed'
                WHERE student_id = %s AND group_id IN (
                    SELECT g.id
                    FROM groups g
                    WHERE g.course_id = %s
                )
            """, (student_id, course_id))
        except Exception as e:
            app.logger.error(f"Ошибка обновления статуса enrollments: {e}")
            flash(f'Ошибка обновления статуса enrollments: {e}', 'danger')

    # Выводим сообщение о завершении курса
    course_completion_message = None
    if all_completed:
        course_completion_message = f"Поздравляем, вы завершили курс! Ваша итоговая оценка: {final_grade_5_scale:.2f}" if final_grade_5_scale is not None else "Вы завершили курс, но итоговая оценка не может быть подсчитана."

    return render_template('student/course_detail.html', course=course, lessons=lessons, course_completion_message=course_completion_message, final_grade=final_grade_5_scale)
# Загрузка задания студентом
@app.route('/student/assignment/<int:assignment_id>/submit', methods=['POST'])
@role_required('Student')
def submit_assignment(assignment_id):
    student_id = session['user_id']
    submission_text = request.form.get('submission_text', '').strip()
    submission_link = request.form.get('submission_link', '').strip()

    # Получение group_id из enrollments
    group_id_row = db.execute("""
        SELECT e.group_id
        FROM enrollments e
        JOIN groups g ON g.id = e.group_id
        JOIN assignments a ON a.lesson_id IN (
            SELECT id FROM lessons WHERE course_id = g.course_id
        )
        WHERE e.student_id = %s AND a.id = %s
        LIMIT 1
    """, (student_id, assignment_id), fetch=True)

    if not group_id_row:
        flash("Вы не записаны на курс, связанный с этим заданием", "danger")
        return redirect(url_for('student_my_courses'))

    group_id = group_id_row[0][0]

    existing = db.execute("""
        SELECT 1 FROM assignment_statuses
        WHERE student_id = %s AND assignment_id = %s
    """, (student_id, assignment_id), fetch=True)

    if existing:
        db.execute("""
            UPDATE assignment_statuses
            SET submission_text = %s, submission_link = %s, status = 'submitted', submitted_at = NOW()
            WHERE student_id = %s AND assignment_id = %s
        """, (submission_text, submission_link, student_id, assignment_id))
    else:
        db.execute("""
            INSERT INTO assignment_statuses (student_id, group_id, assignment_id, submission_text, submission_link, status, submitted_at)
            VALUES (%s, %s, %s, %s, %s, 'submitted', NOW())
        """, (student_id, group_id, assignment_id, submission_text, submission_link))

    flash("Домашнее задание отправлено", "success")
    return redirect(request.referrer or url_for('student_my_courses'))



@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)