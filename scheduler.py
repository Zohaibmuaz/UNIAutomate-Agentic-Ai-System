# scheduler.py (Definitive and Final Version 2.0)
import psycopg2
import random
from datetime import time

# --- CONFIGURATION ---
DB_CONNECTION_STRING = "dbname='university' user='postgres' password='password' host='localhost'"

TIME_SLOTS = [
    {'start': time(9, 0), 'end': time(10, 30)},
    {'start': time(10, 30), 'end': time(12, 0)},
    {'start': time(12, 0), 'end': time(13, 30)},
    {'start': time(13, 30), 'end': time(15, 0)},
    {'start': time(15, 0), 'end': time(16, 30)},
]
DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']

def get_db_connection():
    # This helper function remains the same
    conn = psycopg2.connect(DB_CONNECTION_STRING)
    return conn

def run_query(query, params=None, fetch=None, conn=None):
    # This helper function remains the same
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True
    
    cur = conn.cursor()
    cur.execute(query, params or ())
    result = None
    if fetch == "all": result = cur.fetchall()
    elif fetch == "one": result = cur.fetchone()
    conn.commit()
    cur.close()
    if close_conn:
        conn.close()
    return result

def generate_schedule_logic(semester_name: str, department_name: str) -> str:
    """Generates a truly clash-free timetable using a simple assignment model."""
    print(f"--- Running DEFINITIVE Scheduling Logic v2.0 for {semester_name} ---")

    conn = get_db_connection()
    try:
        # Fetch data
        courses = run_query("SELECT course_id FROM courses WHERE department_id = (SELECT department_id FROM departments WHERE name = %s);", (department_name,), fetch="all", conn=conn)
        teachers = [t[0] for t in run_query("SELECT teacher_id FROM teachers WHERE department_id = (SELECT department_id FROM departments WHERE name = %s);", (department_name,), fetch="all", conn=conn)]
        rooms = [r[0] for r in run_query("SELECT room_id FROM rooms;", fetch="all", conn=conn)]
        semester_id = run_query("SELECT semester_id FROM semesters WHERE name = %s;", (semester_name,), fetch="one", conn=conn)[0]

        if not all([courses, teachers, rooms, semester_id]):
            return "Error: Could not fetch necessary data."

        # --- THE NEW FOOLPROOF LOGIC ---

        # 1. Assign a consistent teacher to each course
        course_teacher_map = {course[0]: random.choice(teachers) for course in courses}

        # 2. Create the full list of lectures to be scheduled (2 per course)
        lectures_to_schedule = []
        for course_id, in courses:
            lectures_to_schedule.append({'course_id': course_id, 'teacher_id': course_teacher_map[course_id]})
            lectures_to_schedule.append({'course_id': course_id, 'teacher_id': course_teacher_map[course_id]})
        
        random.shuffle(lectures_to_schedule)

        # 3. Create a master list of all possible time/room slots and shuffle it
        available_slots = []
        for day in DAYS_OF_WEEK:
            for slot in TIME_SLOTS:
                for room_id in rooms:
                    available_slots.append({'day': day, 'start': slot['start'], 'end': slot['end'], 'room_id': room_id})
        random.shuffle(available_slots)

        # 4. Use simple tracking dictionaries
        teacher_bookings = {} # {(day, start_time): [list of busy teachers]}
        student_group_bookings = {} # {(day, start_time): True} - Simplified to one main group

        # Clear existing timetable
        run_query("DELETE FROM timetable WHERE semester_id = %s;", (semester_id,), conn=conn)

        scheduled_count = 0
        final_timetable = []

        # 5. Assign each lecture to the first truly available slot
        for lecture in lectures_to_schedule:
            slot_found = False
            for i, slot in enumerate(available_slots):
                day, start_time, room_id = slot['day'], slot['start'], slot['room_id']
                teacher_id = lecture['teacher_id']

                # Check for clashes
                is_teacher_busy = teacher_id in teacher_bookings.get((day, start_time), [])
                is_student_group_busy = (day, start_time) in student_group_bookings
                
                if not is_teacher_busy and not is_student_group_busy:
                    # If the slot is free, book it!
                    final_timetable.append((lecture['course_id'], teacher_id, room_id, semester_id, day, start_time, slot['end']))
                    
                    # Mark as booked
                    teacher_bookings.setdefault((day, start_time), []).append(teacher_id)
                    student_group_bookings[(day, start_time)] = True
                    
                    # Remove the used slot from the pool
                    available_slots.pop(i)
                    slot_found = True
                    break # Move to the next lecture
            
            if slot_found:
                scheduled_count += 1
        
        # 6. Bulk insert all scheduled classes at the end
        if final_timetable:
            cur = conn.cursor()
            insert_query = "INSERT INTO timetable (course_id, teacher_id, room_id, semester_id, day_of_week, start_time, end_time) VALUES %s"
            # Use execute_values for efficient bulk insert
            from psycopg2.extras import execute_values
            execute_values(cur, insert_query, final_timetable)
            conn.commit()
            cur.close()

        return f"Successfully generated and saved {scheduled_count} clash-free lectures to the timetable."
    finally:
        if conn:
            conn.close()