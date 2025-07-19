# pdf_generator.py (Complete Version)

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.lib.units import inch
import psycopg2
from datetime import time

DB_CONNECTION_STRING = "dbname='university' user='postgres' password='password' host='localhost'"
TIME_SLOTS = [time(9, 0), time(10, 30), time(12, 0), time(13, 30), time(15, 0)]
DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']

def get_db_connection():
    return psycopg2.connect(DB_CONNECTION_STRING)

def create_timetable_pdf(semester_name: str, department_name: str) -> str:
    """Fetches timetable data for a specific semester/dept and generates a PDF."""
    print(f"--- Generating PDF for {semester_name}, {department_name} ---")

    query = """
        SELECT c.course_name, t.first_name || ' ' || t.last_name AS teacher_name,
               r.room_number AS location, tt.day_of_week, tt.start_time
        FROM timetable tt
        JOIN courses c ON tt.course_id = c.course_id
        JOIN teachers t ON tt.teacher_id = t.teacher_id
        JOIN rooms r ON tt.room_id = r.room_id
        JOIN semesters s ON tt.semester_id = s.semester_id
        JOIN departments d ON c.department_id = d.department_id
        WHERE s.name = %s AND d.name = %s
        ORDER BY tt.day_of_week, tt.start_time;
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(query, (semester_name, department_name))
    schedule_data = cur.fetchall()
    cur.close()
    conn.close()

    if not schedule_data:
        return f"No schedule data found for {semester_name}, {department_name}."

    processed_data = {}
    for row in schedule_data:
        course, teacher, room, day, start_time = row
        processed_data[(day.strip(), start_time)] = {"course": course, "teacher": teacher, "room": room}

    file_name = f"timetable_{department_name.replace(' ', '_')}_{semester_name.replace(' ', '_')}.pdf"
    
    # --- START OF MISSING PDF DRAWING LOGIC ---
    c = canvas.Canvas(file_name, pagesize=landscape(letter))
    width, height = landscape(letter)
    margin = 0.75 * inch

    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(width / 2.0, height - 0.75 * inch, f"Timetable for {department_name} - {semester_name}")

    x_start = margin
    y_start = height - 1.75 * inch
    col_width = (width - margin - x_start) / len(TIME_SLOTS)
    row_height = (y_start - margin) / len(DAYS_OF_WEEK)

    # Draw Header Row (Times)
    c.setFont("Helvetica-Bold", 10)
    for i, slot in enumerate(TIME_SLOTS):
        end_time_hour = slot.hour + 1
        end_time_minute = slot.minute + 30
        if end_time_minute >= 60:
            end_time_hour += 1
            end_time_minute -= 60
        time_str = f"{slot.strftime('%I:%M')} - {time(end_time_hour, end_time_minute).strftime('%I:%M %p')}"
        c.drawCentredString(x_start + (i * col_width) + (col_width / 2), y_start + 10, time_str)

    # Draw Rows and Content
    for i, day in enumerate(DAYS_OF_WEEK):
        y = y_start - (i * row_height)
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(x_start - (margin / 2), y - (row_height / 2), day)
        
        for j, start_time in enumerate(TIME_SLOTS):
            x = x_start + (j * col_width)
            class_info = processed_data.get((day, start_time))
            if class_info:
                text_object = c.beginText(x + 5, y - 20)
                text_object.setFont("Helvetica-Bold", 9)
                text_object.textLine(class_info['course'])
                text_object.setFont("Helvetica", 8)
                text_object.textLine(f"Prof. {class_info['teacher']}")
                text_object.textLine(f"Room: {class_info['room']}")
                c.drawText(text_object)

    # Draw Grid Lines
    c.setStrokeColor(colors.lightgrey)
    # Vertical lines
    for i in range(len(TIME_SLOTS) + 1):
        x = x_start + (i * col_width)
        c.line(x, y_start, x, margin)
    # Horizontal lines
    for i in range(len(DAYS_OF_WEEK) + 1):
        y = y_start - (i * row_height)
        c.line(x_start, y, width - margin, y)

    c.save()
    # --- END OF MISSING PDF DRAWING LOGIC ---

    return f"Successfully generated PDF: {file_name}"

if __name__ == "__main__":
    result = create_timetable_pdf(semester_name="Fall 2025", department_name="Computer Science")
    print(result)