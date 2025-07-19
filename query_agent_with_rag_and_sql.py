import os
import re
import json
from dotenv import load_dotenv

from pydantic import BaseModel, Field
from typing import List

from langchain_community.document_loaders import DirectoryLoader
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

from twilio.rest import Client

from langchain_community.utilities import SQLDatabase
from langchain_core.tools import Tool, StructuredTool
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import MessagesPlaceholder
from langchain_core.messages import AIMessage, HumanMessage

from scheduler import generate_schedule_logic

# --- CONFIGURATION ---
load_dotenv()
DB_CONNECTION_STRING = "postgresql+psycopg2://postgres:password@localhost/university"

# 1. Initialize the LLM
llm = ChatOpenAI(model_name="gpt-4o", temperature=0)

# <<< --- FIX #1: THE ULTIMATE REGEX CLEANER --- >>>
# This function will find and extract the SQL query from the LLM's output,
# no matter what extra text or markdown it adds.
def extract_sql(response_text: str) -> str:
    """Finds and extracts the first SQL query from a text block using regex."""
    # The regex looks for a block of text starting with ```, optionally with 'sql'
    # and extracts everything until the closing ```
    match = re.search(r"```(?:sql)?\s*(.*?)\s*```", response_text, re.DOTALL)
    if match:
        return match.group(1).strip()
    else:
        # If no markdown block is found, return the text as is, cleaning it up.
        return response_text.strip()

# --- TOOL 1: The RAG Tool (No changes here) ---
def create_rag_tool():
    print("Initializing RAG tool...")
    embedding_model = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    vectorstore = Chroma(persist_directory="./chroma_db", embedding_function=embedding_model)
    retriever = vectorstore.as_retriever(search_kwargs={"k": 2})

    rag_prompt = ChatPromptTemplate.from_template(
        """Answer the question based only on the following context:\n{context}\n\nQuestion: {question}"""
    )

    rag_chain = (
        {"context": retriever, "question": RunnablePassthrough()}
        | rag_prompt
        | llm
        | StrOutputParser()
    )
    
    return Tool(
        name="policy_and_course_retriever",
        func=rag_chain.invoke,
        description="Use this ONLY for questions about official written policies, rules, course descriptions, or FAQs found in the department's text documents."
    )

# --- TOOL 2: The SQL Tool (Rebuilt with Few-Shot Prompting) ---
def create_sql_tool():
    print("Initializing SQL tool...")
    db = SQLDatabase.from_uri(DB_CONNECTION_STRING)
    
    # <<< --- FIX #2: FEW-SHOT PROMPTING TO TEACH THE LLM --- >>>
    # We provide examples of good questions and their corresponding correct SQL queries.
    # This helps the LLM generate correct JOINs for complex questions.
    FEW_SHOT_EXAMPLES = """
    **Example 1:**
    User Question: How many students are there?
    SQL Query: SELECT count(*) FROM students;

    **Example 2:**
    User Question: What are the courses and grades for the student with email ayesha.malik@student.edu?
    SQL Query: SELECT c.course_name, g.grade_value FROM students s JOIN enrollments e ON s.student_id = e.student_id JOIN courses c ON e.course_id = c.course_id JOIN grades g ON e.enrollment_id = g.enrollment_id WHERE s.email = 'ayesha.malik@student.edu';

    **Example 3:**
    User Question: Who is the Head of the Computer Science department?
    SQL Query: SELECT t.first_name, t.last_name FROM teachers t JOIN departments d ON t.teacher_id = d.hod_id WHERE d.name = 'Computer Science';
    """
    
    sql_prompt_template = f"""You are a PostgreSQL expert. Given a user question, write a SINGLE, valid SQL query to answer it.
    Do not add any explanation or commentary. Your entire response should be only the SQL code, enclosed in a single ```sql code block.

    Here are some examples of correct queries:
    {FEW_SHOT_EXAMPLES}

    Here is the database schema for your reference:
    {{schema}}

    User Question: {{question}}
    SQL Query:
    """
    sql_prompt = ChatPromptTemplate.from_template(sql_prompt_template)
    
    sql_generation_chain = (
        {"schema": lambda x: db.get_table_info(), "question": RunnablePassthrough()}
        | sql_prompt
        | llm
        | StrOutputParser()
    )
    
    execute_query_tool = Tool(
        name="sql_query_executor",
        func=db.run,
        description="Executes a given SQL query and returns the result."
    )

    # The final chain now generates a query, extracts the pure SQL, and then executes it.
    sql_tool_chain = sql_generation_chain | extract_sql | execute_query_tool

    return Tool(
        name="student_database_query",
        func=sql_tool_chain.invoke,
        description="Use this for ANY question that requires specific, live data about students, teachers, grades, enrollments, or schedules. If the question involves a count, a specific person's data, or current status, this is the correct tool."
    )


# <<< --- NEW: TOOL 3: The Notification Tool --- >>>
def create_notification_tool():
    """Creates a tool that can send WhatsApp messages via Twilio."""
    print("Initializing Notification tool...")
    
    # Load credentials from .env file
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_FROM_NUMBER")
    
    client = Client(account_sid, auth_token)

    def send_whatsapp_message(to: str, message: str) -> str:
        """
        Sends a WhatsApp message to a specified recipient.
        'to' must be a phone number in E.164 format (e.g., 'whatsapp:+14155238886').
        'message' is the text to be sent.
        """
        try:
            message_instance = client.messages.create(
                from_=from_number,
                body=message,
                to=to
            )
            return f"Message sent successfully to {to}. SID: {message_instance.sid}"
        except Exception as e:
            return f"Failed to send message: {e}"

    return StructuredTool.from_function(
        name="whatsapp_sender",
        func=send_whatsapp_message,
        description="Use this tool to send a WhatsApp notification. It requires a recipient phone number (prefixed with 'whatsapp:') and a message text."
    )


# <<< --- NEW: TOOL 4: The Results Submission Tool --- >>>
# Define the structured input for our safe tool
class GradeInput(BaseModel):
    student_id: int = Field(description="The unique ID of the student.")
    grade: str = Field(description="The final grade, such as 'A', 'B+', or 'C'.")

class SubmitGradesInput(BaseModel):
    course_id: int = Field(description="The unique ID for the course.")
    grades: List[GradeInput] = Field(description="A list of student grades to submit.")


def create_results_tool():
    """Creates a safe tool for submitting student grades."""
    print("Initializing Results tool...")
    db = SQLDatabase.from_uri(DB_CONNECTION_STRING)

    def submit_grades(course_id: int, grades: List[GradeInput]) -> str:
        """
        Safely submits final grades for multiple students in a specific course.
        It finds the correct enrollment record and updates the corresponding grade.
        """
        # FIX #1: Change deprecated .dict() to .model_dump()
        grades_list = [g.model_dump() for g in grades]
        
        updated_count = 0
        errors = []
        for grade_entry in grades_list:
            student_id = grade_entry['student_id']
            grade_value = grade_entry['grade']
            
            # FIX #2: Change parameter style from %(name)s to :name
            query = """
                WITH TargetEnrollment AS (
                    SELECT enrollment_id FROM enrollments WHERE student_id = :student_id AND course_id = :course_id
                )
                INSERT INTO grades (enrollment_id, grade_value)
                SELECT enrollment_id, :grade_value FROM TargetEnrollment
                ON CONFLICT (enrollment_id) DO UPDATE SET grade_value = EXCLUDED.grade_value;
            """
            params = {'student_id': student_id, 'course_id': course_id, 'grade_value': grade_value}
            
            try:
                # The db.run() method works perfectly with the :name style
                db.run(query, parameters=params)
                updated_count += 1
            except Exception as e:
                errors.append(f"Error for student {student_id}: {e}")

        if errors:
            return f"Completed with errors. Successfully updated {updated_count} grades. Errors: {', '.join(errors)}"
        return f"Successfully submitted grades for {updated_count} students in course {course_id}."

    # Use a StructuredTool to handle the complex input
    return StructuredTool.from_function(
        func=submit_grades,
        name="grade_submitter",
        description="Use this to submit final grades for one or more students in a single course. Requires a course ID and a list of student IDs and their grades.",
        args_schema=SubmitGradesInput
    )


# <<< --- NEW: TOOL 5: The Analytics Tool --- >>>
def create_analytics_tool():
    """Creates a tool that can answer analytical questions about the database."""
    print("Initializing Analytics tool...")
    db = SQLDatabase.from_uri(DB_CONNECTION_STRING)

    ANALYTICS_EXAMPLES = """
    **Example 1:**
    User Question: How many students are in each department?
    SQL Query: SELECT d.name, count(s.student_id) AS number_of_students FROM students s JOIN departments d ON s.department_id = d.department_id GROUP BY d.name;

    **Example 2:**
    User Question: What are the top 3 courses with the most students?
    SQL Query: SELECT c.course_name, count(e.student_id) AS enrollment_count FROM enrollments e JOIN courses c ON e.course_id = c.course_id GROUP BY c.course_name ORDER BY enrollment_count DESC LIMIT 3;
    """

    analytics_prompt_template = f"""You are a PostgreSQL expert specializing in analytics. Given a user question, write a SINGLE, valid SQL query to answer it.
    Your query will often involve using functions like COUNT, AVG, GROUP BY, and ORDER BY.
    Do not add any explanation or commentary. Your entire response should be only the SQL code, enclosed in a single ```sql code block.

    Here are some examples of correct analytical queries:
    {ANALYTICS_EXAMPLES}

    Here is the database schema for your reference:
    {{schema}}

    User Question: {{question}}
    SQL Query:
    """
    analytics_prompt = ChatPromptTemplate.from_template(analytics_prompt_template)

    analytics_chain = (
        {"schema": lambda x: db.get_table_info(), "question": RunnablePassthrough()}
        | analytics_prompt
        | llm
        | StrOutputParser()
    )

    execute_query_tool = Tool(name="sql_executor", func=db.run, description="Executes an SQL query.")

    analytics_tool_chain = analytics_chain | extract_sql | execute_query_tool

    return Tool(
        name="database_analyzer",
        func=analytics_tool_chain.invoke,
        description="Use this for questions that require calculations, aggregations, or analytics, such as finding averages, counts, or rankings. For simple data lookups, use the 'student_database_query' tool."
    )


# <<< --- NEW: TOOL 6: The Timetable Generation Tool --- >>>
def create_timetable_tool():
    """Creates a tool that can generate a class schedule."""
    print("Initializing Timetable tool...")

    class TimetableInput(BaseModel):
        semester_name: str = Field(description="The name of the semester, e.g., 'Fall 2025'.")
        department_name: str = Field(description="The name of the department, e.g., 'Computer Science'.")


    return StructuredTool.from_function(
        func=generate_schedule_logic,
        name="generate_schedule_logic",
        description="Generates and saves the master class schedule for a given semester and department. This is a heavy, long-running task.",
        args_schema=TimetableInput
    )




# --- AGENT SETUP (No changes here) ---
policy_tool = create_rag_tool()
sql_tool = create_sql_tool()
notification_tool = create_notification_tool()
results_tool = create_results_tool()
analytics_tool = create_analytics_tool()
timetable_tool = create_timetable_tool()
tools = [policy_tool, sql_tool,notification_tool,results_tool,analytics_tool,timetable_tool]

# Update the main agent prompt to include the new tool's purpose
AGENT_PROMPT = """
You are an expert university department assistant with a suite of tools. You MUST follow these rules:

1.  **To ANSWER a question, decide the source:**
    - General policies/rules? -> Use `policy_retriever`.
    - Simple data lookup? -> Use `student_database_query`.
    - Calculations, averages, or counts? -> Use `database_analyzer`.

2.  **To PERFORM an action, identify the action type:**
    - Sending a message? -> Use `whatsapp_sender`.
    - Submitting grades? -> Use `grade_submitter`.
    - Generating the master schedule? -> Use `timetable_generator`.

3.  **IMPORTANT SAFETY RULE:** You are strictly forbidden from writing your own SQL queries to modify the database. ALL grade changes MUST go through the `grade_submitter` tool.
"""

prompt = ChatPromptTemplate.from_messages([
    ("system", AGENT_PROMPT),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

agent = create_tool_calling_agent(llm, tools, prompt)

agent_executor = AgentExecutor(
    agent=agent,
    tools=tools,
    verbose=True,
    handle_parsing_errors=True
)

if __name__ == "__main__":
    print("Advanced Department Agent is ready. Ask a question or type 'exit' to quit.")
    chat_history = []
    while True:
        user_question = input("You: ") 
        # I need to submit final grades for the course ID 23. Give student ID 1 an 'A', and student ID 2 a 'B+'
        if user_question.lower() == 'exit':
            break
        
        response = agent_executor.invoke({
            "input": user_question,
            "chat_history": chat_history
        })
        
        # Add interaction to chat history
        chat_history.append(HumanMessage(content=user_question))
        chat_history.append(AIMessage(content=response["output"]))

        print(f"Agent: {response['output']}")

