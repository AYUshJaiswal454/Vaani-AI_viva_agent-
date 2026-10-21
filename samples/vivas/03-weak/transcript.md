# DBMS mid-semester viva: Sneha Patil (23CS10003)

Total: **19 / 100** (ai score, confidence medium)

- **[00:00.3] Examiner** `system:greeting`: Hello Sneha. I'm Vaani, your examiner for Database Management Systems today. Before we begin, please say your full name and your roll number.
- **[00:09.6] Student**: My name is Snohaputul and my roll number is 23 CS1. 1 0,003
- **[00:15.8] Examiner** `system:rules,bank:DBMS-W-02`: Thank you. A quick note before we start: this viva has five short parts and lasts at most fifteen minutes. Answer out loud, in your own words. It's completely fine to say you don't know, or to ask me to repeat a question. To begin, briefly describe a project you have worked on, ideally one that stores or processes data, and explain your role in it.
- **[00:37.1] Student**: i did a website project with my friends, it was about students. i did the front end part mostly
- **[00:43.7] Examiner** `system:hint_intro,bank:DBMS-W-02/h1`: Here's a hint. Tell me a little more: what did it do, what data did it keep, and which part did you build yourself?
- **[00:51.4] Student**: in other words it says what the project is.
- **[00:55.7] Examiner** `system:ack,system:to_fundamentals,bank:DBMS-F-04`: Alright. Let's move on to some fundamentals. What is normalisation, and why do we normalise a database?
- **[01:03.8] Student**: It is something related to normalisation, it is used in the database for the data It is important
- **[01:10.5] Examiner** `system:hint_intro,bank:DBMS-F-04/h1`: Here's a hint. Think about storing a department's phone number on every employee's row, and then the phone number changes.
- **[01:18.8] Student**: organism tables to reduce redundancy
- **[01:21.6] Examiner** `system:ack,bank:DBMS-F-08`: Thank you. What is an index in a database and why is it used?
- **[01:26.3] Student**: It is something related to indexing, it is used in the database for the data it is important
- **[01:32.9] Examiner** `system:hint_intro,bank:DBMS-F-08/h1`: Here's a hint. Think of how you find a topic in a 900-page textbook without reading every page.
- **[01:40.6] Student**: hmm, i'm not sure
- **[01:43.1] Examiner** `system:idk_ack,bank:DBMS-F-09`: That's alright. What is an entity-relationship diagram, and what are entities, attributes and relationships?
- **[01:50.8] Student**: It is something related or model, it is used in the database for the data it is important
- **[01:57.2] Examiner** `system:hint_intro,bank:DBMS-F-09/h1`: Here's a hint. Think of a college office: what does it keep records about, and how are those records related?
- **[02:04.3] Student**: diagram, in other words conceptual/visual model of the database design
- **[02:10.4] Examiner** `system:ack,bank:DBMS-F-03`: Got it. What do the ACID properties of a transaction mean?
- **[02:14.7] Student**: Consistency means all copies of data are the same that is replication consistency, either I think.
- **[02:21.1] Examiner** `system:hint_intro,bank:DBMS-F-03/h1`: Here's a hint. Imagine transferring 500 rupees from one bank account to another and the power goes off halfway.
- **[02:29.2] Student**: and uponicity?
- **[02:32.1] Examiner** `system:ack,bank:DBMS-F-11`: Okay, thanks. What is the difference between DELETE, TRUNCATE and DROP?
- **[02:37.3] Student**: It is something related to SQL, it is used in the database for the data it is important
- **[02:44.5] Examiner** `system:hint_intro,bank:DBMS-F-11/h1`: Here's a hint. Think of an exam answer sheet: erasing some answers, erasing all answers, or throwing away the sheet.
- **[02:53.1] Student**: delete rows, and other words delete removes selected rows
- **[02:57.4] Examiner** `system:ack,bank:DBMS-F-01`: Noted. What is the difference between a primary key and a foreign key?
- **[03:02.5] Student**: primary key uniquely identifies a draw
- **[03:05.9] Examiner** `system:hint_intro,bank:DBMS-F-01/h1`: Here's a hint. Think of a Students table and an Enrolments table: how does each row in Enrolments know which student it belongs to?
- **[03:13.9] Student**: HMM, I'm not sure
- **[03:16.7] Examiner** `system:idk_ack,bank:DBMS-F-06`: That's alright. What is the difference between the WHERE clause and the HAVING clause in SQL?
- **[03:22.4] Student**: it is something related to SQL, it is used in the database for the data it is important
- **[03:29.0] Examiner** `system:hint_intro,bank:DBMS-F-06/h1`: Here's a hint. Think about the order in which the database processes the different clauses of a query.
- **[03:35.3] Student**: WHERE filters rose before grouping
- **[03:37.8] Examiner** `system:ack,bank:DBMS-F-12`: Okay. How does a database recover after a crash? What role does the log play?
- **[03:43.7] Student**: I don't know this one
- **[03:45.5] Examiner** `system:idk_ack,bank:DBMS-F-02`: That's fine. Explain super key, candidate key and primary key, and how they are related.
- **[03:52.3] Student**: super key
- **[03:55.1] Examiner** `system:hint_intro,bank:DBMS-F-02/h1`: Here's a hint. Start from the job a key does in a table, then ask whether every column in a key set is really needed.
- **[04:02.7] Student**: HMM, I'm not sure
- **[04:05.2] Examiner** `system:idk_ack,bank:DBMS-F-13`: Okay, no worries. What is the difference between a shared lock and an exclusive lock?
- **[04:11.0] Student**: It is something related to concurrency, it is used in the database for the data. It is important
- **[04:17.5] Examiner** `system:hint_intro,bank:DBMS-F-13/h1`: Here's a hint. Think about a notice board in college: what is safe for many people to do at once, and what isn't?
- **[04:24.7] Student**: shared
- **[04:27.3] Examiner** `system:ack,system:depth_intro,bank:DBMS-F-05`: I see. Let's go a little deeper into normalisation. Explain first, second and third normal form in simple terms.
- **[04:35.6] Student**: 3NF means a table has exactly three columns or three tables, I think
- **[04:42.0] Examiner** `system:hint_intro,bank:DBMS-F-05/h1`: Here's a hint. For each normal form, ask what a non-key column is allowed to depend on.
- **[04:48.3] Student**: atomic, another words 1 and F
- **[04:51.0] Examiner** `system:ack,bank:DBMS-F-14`: Got it. What is a dirty read? Which isolation level prevents it?
- **[04:55.7] Student**: It is something related to transactions, it is used in the database for the data It is important
- **[05:02.1] Examiner** `system:hint_intro,bank:DBMS-F-14/h1`: Here's a hint. Imagine you check a friend's bank balance while their deposit is still being processed, and then the deposit fails.
- **[05:10.1] Student**: HMM, I'm not sure
- **[05:12.7] Examiner** `system:idk_ack,bank:DBMS-F-07`: That's fine. Explain the different types of joins: inner join, left outer join and full outer join.
- **[05:19.6] Student**: it is something related to SQL, it is used in the database for the data it is important
- **[05:25.8] Examiner** `system:hint_intro,bank:DBMS-F-07/h1`: Here's a hint. Picture two overlapping circles, one per table.
- **[05:31.0] Student**: hmm, i'm not sure
- **[05:33.8] Examiner** `system:idk_ack,bank:DBMS-F-10`: Okay, no worries. What is a deadlock in a database, and how can it be handled?
- **[05:39.6] Student**: deadlock and starvation are the same, I think
- **[05:43.5] Examiner** `system:hint_intro,bank:DBMS-F-10/h1`: Here's a hint. Picture two cars meeting in the middle of a narrow one-lane bridge.
- **[05:48.7] Student**: waiting for each other, in other words to or more transactions wait for each other's locks forever
- **[05:54.6] Examiner** `system:ack,bank:DBMS-D-05`: Right. How does a query optimiser decide how to execute a SQL query? What information does it use?
- **[06:01.8] Student**: I don't know this one
- **[06:03.6] Examiner** `system:idk_ack,bank:DBMS-D-04`: No problem. How does multiversion concurrency control (MVCC) work, and why do databases like PostgreSQL use it?
- **[06:13.4] Student**: new version, in other words writers create new role versions instead of overwriting
- **[06:18.9] Examiner** `system:hint_intro,bank:DBMS-D-04/h1`: Here's a hint. Think about how a library could let one person read an old edition while the new edition is being printed.
- **[06:26.2] Student**: HMM, I'm not sure
- **[06:29.0] Examiner** `system:idk_ack,bank:DBMS-D-03`: That's fine. What is a lossless-join decomposition, and how do you check that splitting a table did not lose information?
- **[06:37.3] Student**: I don't know this one
- **[06:39.1] Examiner** `system:idk_ack,system:different_area,bank:DBMS-D-07`: Okay, no worries. Let's try a different area. What is a weak entity? How is it identified and represented in tables?
- **[06:48.1] Student**: cannot be uniquely identified by its own attributes.
- **[06:52.0] Examiner** `system:hint_intro,bank:DBMS-D-07/h1`: Here's a hint. Think of rooms numbered 101, 102 in several different buildings.
- **[06:58.6] Student**: HMM, I'm not sure
- **[07:01.4] Examiner** `system:idk_ack,system:to_applied_code,bank:DBMS-A-02`: That's alright. Now let's try a practical problem. I've put some code on your screen. Please think out loud as you work through it. A college fest site sells 500 tickets. During the rush, 520 tickets got sold. The code reads the remaining count, checks it is above zero, and then decrements it. Reason aloud: what went wrong, and how would you fix it?
- **[07:23.6] Student**: I don't know this one.
- **[07:25.4] Examiner** `system:idk_ack,system:different_area,bank:DBMS-A-03`: No problem. Let's try a different area. Design the tables for a library system: members borrow books, a book can have many copies, and we must know who has which copy and when it is due. Talk me through your tables and keys.
- **[07:39.5] Student**: copy table, another one separate book
- **[07:42.8] Examiner** `system:hint_intro,bank:DBMS-A-03/h1`: Here's a hint. Start by listing the real-world things involved. Is 'a book' the same thing as 'the copy on the shelf'?
- **[07:50.1] Student**: HMM, I'm not sure
- **[07:53.2] Examiner** `system:idk_ack,bank:DBMS-A-01`: That's fine. Here is a slow query on an orders table with ten million rows. It takes eight seconds. Walk me through how you would find out why it is slow and how you would fix it.
- **[08:03.6] Student**: Check the plan with explain to see if all tables can
- **[08:07.3] Examiner** `system:hint_intro,bank:DBMS-A-01/h1`: Here's a hint. Before changing anything, what tool would show you how the database is actually running this query?
- **[08:14.6] Student**: 8 mm, I'm not sure.
- **[08:17.4] Examiner** `system:idk_ack,bank:DBMS-A-04`: Okay, no worries. This query is supposed to show each department with its number of employees, including departments with no employees. But departments with zero employees are missing. Explain why and how to fix it.
- **[08:30.9] Student**: I don't know this one.
- **[08:32.9] Examiner** `system:idk_ack,system:different_area,bank:DBMS-A-05`: That's alright. Let's try a different area. A payment app moves money from wallet A to wallet B with two separate UPDATE statements and no transaction. Occasionally money disappears. Reason aloud about the causes and design a fix.
- **[08:46.9] Student**: It is something related to transactions. It is used in the database for the data. It is important
- **[08:54.0] Examiner** `system:hint_intro,bank:DBMS-A-05/h1`: Here's a hint. Think about exactly what state the two balances are in if the server dies at the comment line.
- **[09:00.8] Student**: HMM, I'm not sure
- **[09:03.5] Examiner** `system:idk_ack,system:to_wrapup,system:wrapup_q`: No problem. We're nearly done. Is there anything you'd like to add or clarify about any of your answers?
- **[09:11.3] Student**: I think I've covered everything. Thank you.
- **[09:14.7] Examiner** `system:close`: Thank you. That's the end of your viva. Your answers will be reviewed and your faculty will share the results. You can close this window now.

## Scores

| Dimension | Score | Reason |
|---|---|---|
| conceptual | 1 / 5 | Covered 13 of 64 key points across 18 questions; strongest at 03:16 (SQL), weakest at 01:21 (Indexing). |
| depth | 0 / 5 | 0 of 0 follow-ups answered with sound reasoning; reasons or examples given in 2% of answers. |
| problem_solving | 1 / 5 | Applied problem: identified 0 of 4 expected steps. |
| practical | 1 / 5 | Project described in general terms without naming technologies; own contribution unclear. |
| communication | 3 / 5 | 100% of answers on the question; 4% organised with reasons, steps or examples. |

## Feedback

- Strong on sql: at 03:35 you covered WHERE filters rows before grouping, HAVING filters groups after GROUP BY.
- Revise indexing: the answer at 01:26 missed data structure that speeds up searching/lookups, usually a B-tree/B+ tree or hash structure.
- When asked 'why' or 'what if', explain the reasoning and the trade-off, not only the definition.
- In the applied problem you made a start; also consider race condition.
