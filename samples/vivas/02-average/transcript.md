# DBMS mid-semester viva: Rohan Gupta (23CS10002)

Total: **50 / 100** (ai score, confidence medium)

- **[00:00.3] Examiner** `system:greeting`: Hello Rohan. I'm Vaani, your examiner for Database Management Systems today. Before we begin, please say your full name and your roll number.
- **[00:09.5] Student**: My name is Rowan Gupta and my role number is 23 CS10002.
- **[00:15.7] Examiner** `system:rules,bank:DBMS-W-01`: Thank you. A quick note before we start: this viva has five short parts and lasts at most fifteen minutes. Answer out loud, in your own words. It's completely fine to say you don't know, or to ask me to repeat a question. Let's start with your work. In about a minute, tell me about a project where you stored data in a database: what it did, how the data was organised, and what your own part was.
- **[00:38.9] Student**: i made a library management mini project in Java with mysq l, it stores books and members, and i wrote the issue and returned part. There were tables for books and students.
- **[00:52.1] Examiner** `system:ack,bank:DBMS-W-01/f1`: Alright. Which table or collection in that project was the most important, and what were its main fields?
- **[00:59.3] Student**: a project, for instance, we actually ran into exactly this.
- **[01:09.4] Examiner** `system:ack,system:to_fundamentals,bank:DBMS-F-10`: Thank you. Let's move on to some fundamentals. What is a deadlock in a database, and how can it be handled?
- **[01:17.3] Student**: some, so, waiting for each other, in other words to or more transactions wait for each other's locks forever.
- **[01:24.5] Examiner** `system:hint_intro,bank:DBMS-F-10/h1`: Here's a hint. Picture two cars meeting in the middle of a narrow one-lane bridge.
- **[01:29.7] Student**: wait for graph in other words detection with a wait for graph and aborting a victim
- **[01:36.3] Examiner** `system:ack,bank:DBMS-F-12`: I see. How does a database recover after a crash? What role does the log play?
- **[01:42.2] Student**: Some, sure, changes are recorded in a log before data pages since are due to committed transactions.
- **[01:49.4] Examiner** `system:ack,bank:DBMS-F-12/f1`: Got it. Why must the log record be written to disk before the data page it describes?
- **[01:55.2] Student**: Okay, so can not undo, in other words otherwise a crash could leave a change on disk with no way to undo it.
- **[02:03.3] Examiner** `system:ack,bank:DBMS-F-02`: Okay, thanks. Explain super key, candidate key and primary key, and how they are related.
- **[02:10.5] Student**: super key, also, minimal, in other words, candidate key, a simple example is a banking app, where getting this wrong means money goes missing.
- **[02:21.3] Examiner** `system:hint_intro,bank:DBMS-F-02/h1`: Here's a hint. Start from the job a key does in a table, then ask whether every column in a key set is really needed.
- **[02:28.7] Student**: minimal, in other words, candidate key
- **[02:31.9] Examiner** `system:ack,bank:DBMS-F-13`: Noted. What is the difference between a shared lock and an exclusive lock?
- **[02:36.9] Student**: I'm so shared.
- **[02:39.2] Examiner** `system:hint_intro,bank:DBMS-F-13/h1`: Here's a hint. Think about a notice board in college: what is safe for many people to do at once, and what isn't?
- **[02:46.4] Student**: exclusive
- **[02:49.9] Examiner** `system:ack,bank:DBMS-F-05`: Right. Explain first, second and third normal form in simple terms.
- **[02:54.9] Student**: I am not sure about this one.
- **[02:57.7] Examiner** `system:idk_ack,bank:DBMS-F-14`: No problem. What is a dirty read? Which isolation level prevents it?
- **[03:02.8] Student**: I am not sure about this one.
- **[03:05.5] Examiner** `system:idk_ack,system:different_area,bank:DBMS-F-09`: That's fine. Let's try a different area. What is an entity-relationship diagram, and what are entities, attributes and relationships?
- **[03:15.3] Student**: sum, schwar, diagram, in other words conceptual/visual model of the database design, also real world, in other words entity.
- **[03:26.1] Examiner** `system:hint_intro,bank:DBMS-F-09/h1`: Here's a hint. Think of a college office: what does it keep records about, and how are those records related?
- **[03:33.2] Student**: real world, in other words entity
- **[03:36.3] Examiner** `system:ack,bank:DBMS-F-08`: Thank you. What is an index in a database and why is it used?
- **[03:40.9] Student**: I am not sure about this one.
- **[03:44.2] Examiner** `system:idk_ack,bank:DBMS-F-04`: That's alright. What is normalisation, and why do we normalise a database?
- **[03:49.7] Student**: Right, organising tables to reduce redundancy, also insertion anomaly in other words avoid insertion, update and deletion anomalies.
- **[04:00.3] Examiner** `system:hint_intro,bank:DBMS-F-04/h1`: Here's a hint. Think about storing a department's phone number on every employee's row, and then the phone number changes.
- **[04:08.3] Student**: Avoid insertion, update and deletion anomalies
- **[04:12.5] Examiner** `system:ack,bank:DBMS-F-03`: Got it. What do the ACID properties of a transaction mean?
- **[04:16.9] Student**: atom, right air-tarmicity, also consistency
- **[04:21.1] Examiner** `system:hint_intro,bank:DBMS-F-03/h1`: Here's a hint. Imagine transferring 500 rupees from one bank account to another and the power goes off halfway.
- **[04:29.1] Student**: consistency
- **[04:32.2] Examiner** `system:ack,system:depth_intro,bank:DBMS-F-11`: Okay, thanks. Let's go a little deeper into SQL. What is the difference between DELETE, TRUNCATE and DROP?
- **[04:39.7] Student**: Some delete rows, in other words delete remove selected rows
- **[04:44.9] Examiner** `system:hint_intro,bank:DBMS-F-11/h1`: Here's a hint. Think of an exam answer sheet: erasing some answers, erasing all answers, or throwing away the sheet.
- **[04:53.6] Student**: TRUNCATE removes all rows, keeps the table structure is faster DDL
- **[05:00.3] Examiner** `system:ack,bank:DBMS-F-01`: Noted. What is the difference between a primary key and a foreign key?
- **[05:05.5] Student**: right, primary key uniquely identifies each row also, no null, in other words primary key cannot be null, in our college or the primary project, for instance, we actually ran into exactly this.
- **[05:19.8] Examiner** `system:ack,bank:DBMS-F-01/f1`: Right. Can a foreign key column contain NULL values or duplicate values? Why?
- **[05:25.6] Student**: I'm so duplicate in other words foreign keys may repeat
- **[05:30.5] Examiner** `system:ack,bank:DBMS-D-06`: Okay. Explain the steal and no-force policies in buffer management and why they make recovery need both undo and redo.
- **[05:38.5] Student**: some, schwarp steal, and that is why not flushed, in other words no force, in our college year project, for instance, we actually ran into exactly this.
- **[05:49.7] Examiner** `system:ack,bank:DBMS-D-06/f1`: Alright. What if a system used no-steal and force instead? What would be simpler and what would be worse?
- **[05:56.9] Student**: ok, so no undo or redo needed
- **[05:60.0] Examiner** `system:ack,bank:DBMS-D-05`: Thank you. How does a query optimiser decide how to execute a SQL query? What information does it use?
- **[06:07.9] Student**: Okay, so generate alternative plans also lowest cost, in other words estimates cost of each plan and picks the cheapest, a simple example is a banking app. We're getting this wrong means money goes missing.
- **[06:23.8] Examiner** `system:hint_intro,bank:DBMS-D-05/h1`: Here's a hint. Think about how you would choose a route across a city if you had a rough idea of traffic on each road.
- **[06:30.5] Student**: estimates cost of each plan and picks the cheapest
- **[06:34.6] Examiner** `system:ack,bank:DBMS-D-08`: I see. When would you choose a surrogate key, like an auto-increment ID, instead of a natural key such as a roll number or email?
- **[06:43.4] Student**: Some right natural keys can change, also compact in other words surrogate keys are stable, small and fast to joy and slash index.
- **[06:53.1] Examiner** `system:hint_intro,bank:DBMS-D-08/h1`: Here's a hint. Think about ten related tables, four years of data, and what the key column in each of them would hold.
- **[07:00.9] Student**: surrogate keys are stable, small and fast to joy and slash index
- **[07:06.4] Examiner** `system:ack,bank:DBMS-D-07`: Got it. What is a weak entity? How is it identified and represented in tables?
- **[07:12.1] Student**: I am not sure about this one
- **[07:14.8] Examiner** `system:idk_ack,bank:DBMS-D-04`: That's fine. How does multiversion concurrency control (MVCC) work, and why do databases like PostgreSQL use it?
- **[07:24.3] Student**: I'm sure new version in other words writers create new reversions instead of O. Overwriting, also point in time, in other words we foresee a consistent Snapshot
- **[07:35.9] Examiner** `system:ack,bank:DBMS-D-04/f1`: Noted. What problem appears if old versions are never cleaned up?
- **[07:40.7] Student**: (shoe or table bloat) slower scans wasted storage
- **[07:45.3] Examiner** `system:ack,bank:DBMS-D-04/f2`: Right. Can two concurrent MVCC transactions still conflict? Give an example.
- **[07:51.6] Student**: okay, so write write conflict on the same row, or write skew
- **[07:56.9] Examiner** `system:ack,bank:DBMS-D-03`: Okay. What is a lossless-join decomposition, and how do you check that splitting a table did not lose information?
- **[08:05.0] Student**: I'm so, joining the parts returns exactly the original relation
- **[08:10.1] Examiner** `system:hint_intro,bank:DBMS-D-03/h1`: Here's a hint. Think about what the columns shared by the two new tables must look like.
- **[08:15.6] Student**: Common attributes must be a key of at least one part
- **[08:20.2] Examiner** `system:ack,system:to_applied_code,bank:DBMS-A-04`: Alright. Now let's try a practical problem. I've put some code on your screen. Please think out loud as you work through it. This query is supposed to show each department with its number of employees, including departments with no employees. But departments with zero employees are missing. Explain why and how to fix it.
- **[08:39.6] Student**: Right, inner join removes departments without matching employees.
- **[08:44.8] Examiner** `system:hint_intro,bank:DBMS-A-04/h1`: Here's a hint. Ask yourself which rows the JOIN produces for a department that has no employees at all.
- **[08:52.1] Student**: Use left join from departments.
- **[08:55.0] Examiner** `system:ack,bank:DBMS-A-05`: Thank you. A payment app moves money from wallet A to wallet B with two separate UPDATE statements and no transaction. Occasionally money disappears. Reason aloud about the causes and design a fix.
- **[09:07.4] Student**: Some so crash between, in other words no atomicity, also wrap both in a single transaction with begin/commit. roll back on failure.
- **[09:17.9] Examiner** `system:hint_intro,bank:DBMS-A-05/h1`: Here's a hint. Think about exactly what state the two balances are in if the server dies at the comment line.
- **[09:24.8] Student**: wrap both in a single transaction with begin/commit, roll back on Confail your
- **[09:31.4] Examiner** `system:ack,bank:DBMS-A-02`: I see. A college fest site sells 500 tickets. During the rush, 520 tickets got sold. The code reads the remaining count, checks it is above zero, and then decrements it. Reason aloud: what went wrong, and how would you fix it?
- **[09:46.9] Student**: > right, race condition, also, single update in other words make check-in. can be determined to stomach.
- **[09:53.6] Examiner** `system:hint_intro,bank:DBMS-A-02/h1`: Here's a hint. Imagine two buyers whose requests reach the server at exactly the same millisecond. Step through what each one sees.
- **[10:02.0] Student**: where remaining > 0, in other words make check and decrement atomic.
- **[10:07.4] Examiner** `system:ack,bank:DBMS-A-01`: Got it. Here is a slow query on an orders table with ten million rows. It takes eight seconds. Walk me through how you would find out why it is slow and how you would fix it.
- **[10:17.3] Student**: I am not sure about this one.
- **[10:19.9] Examiner** `system:idk_ack,bank:DBMS-A-03`: That's fine. Design the tables for a library system: members borrow books, a book can have many copies, and we must know who has which copy and when it is due. Talk me through your tables and keys.
- **[10:31.7] Student**: system, okay, so copy table, in other words separate book also, member it in other words member table with member underscore it primary key.
- **[10:42.1] Examiner** `system:hint_intro,bank:DBMS-A-03/h1`: Here's a hint. Start by listing the real-world things involved. Is 'a book' the same thing as 'the copy on the shelf'?
- **[10:49.7] Student**: member table with member_primary key
- **[10:53.7] Examiner** `system:ack,system:to_wrapup,system:wrapup_q`: Noted. We're nearly done. Is there anything you'd like to add or clarify about any of your answers?
- **[11:00.8] Student**: No, I think I've covered everything, thank you.
- **[11:04.7] Examiner** `system:close`: Thank you. That's the end of your viva. Your answers will be reviewed and your faculty will share the results. You can close this window now.

## Scores

| Dimension | Score | Reason |
|---|---|---|
| conceptual | 2 / 5 | Covered 28 of 65 key points across 18 questions; strongest at 04:32 (SQL), weakest at 02:49 (Normalisation). |
| depth | 3 / 5 | 5 of 5 follow-ups answered with sound reasoning; reasons or examples given in 14% of answers. |
| problem_solving | 2 / 5 | Applied problem: identified 2 of 3 expected steps (inner JOIN removes departments without matching employees, use LEFT JOIN from departments). |
| practical | 3 / 5 | Project described concretely (named java); own role explained. |
| communication | 3 / 5 | 100% of answers on the question; 15% organised with reasons, steps or examples. |

## Feedback

- Strong on sql: at 04:53 you covered DELETE removes selected rows, TRUNCATE removes all rows, keeps the table structure, is faster/DDL, DROP removes the whole table including structure.
- Revise normalisation: the answer at 02:54 missed 1NF, 2NF.
- In the applied problem you identified inner JOIN removes departments without matching employees; also consider COUNT.
