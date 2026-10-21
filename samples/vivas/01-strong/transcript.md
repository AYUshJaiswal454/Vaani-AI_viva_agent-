# DBMS mid-semester viva: Ananya Krishnan (23CS10001)

Total: **89 / 100** (ai score, confidence low)

- **[00:00.3] Examiner** `system:greeting`: Hello Ananya. I'm Vaani, your examiner for Database Management Systems today. Before we begin, please say your full name and your roll number.
- **[00:09.7] Student**: My name is Onan Akrishnan and my roll number is 23 CS1. 101
- **[00:15.9] Examiner** `system:rules,bank:DBMS-W-01`: Thank you. A quick note before we start: this viva has five short parts and lasts at most fifteen minutes. Answer out loud, in your own words. It's completely fine to say you don't know, or to ask me to repeat a question. Let's start with your work. In about a minute, tell me about a project where you stored data in a database: what it did, how the data was organised, and what your own part was.
- **[00:39.3] Student**: i built a canteen pre-ordering app for our college using React. Node.js and POSTGR SQL, students order and pay before the break and the canteen sees a queue, i designed the database from a user's table. a menu item stable, and an order stable with foreign keys to both. plus and order items stable for the many to many part, i also wrote the payment web who can fix the bug where two orders got the same token number by using a database sequence. Around 300 students used it in the first week.
- **[01:13.0] Examiner** `system:ack,bank:DBMS-W-01/f1`: Alright. Which table or collection in that project was the most important, and what were its main fields?
- **[01:19.7] Student**: ok, so names are concrete, table and fields in our college project for instance, we actually run into exactly this
- **[01:28.1] Examiner** `system:ack,system:to_fundamentals,bank:DBMS-F-12`: Thank you. Let's move on to some fundamentals. How does a database recover after a crash? What role does the log play?
- **[01:36.6] Student**: Changes are recorded in a log before data pages, also replay, in other words redo committed transactions, which means rollback in other words and do uncommitted transactions, for example, in the real way reservation system this matters because thousands of people boot at the same time
- **[01:54.2] Examiner** `system:ack,bank:DBMS-F-12/f1`: I see. Why must the log record be written to disk before the data page it describes?
- **[01:59.7] Student**: So, cannot undo, in other words otherwise a crash could leave a change on disk with no way to undo it?
- **[02:06.4] Examiner** `system:ack,bank:DBMS-F-10`: Got it. What is a deadlock in a database, and how can it be handled?
- **[02:11.0] Student**: okay, so waiting for each other, in other words to or more transactions wait for each other's looks forever, also, detection with a wait for graph and aborting a victim. A simple example is a banking app, where getting this wrong means money goes missing.
- **[02:26.3] Examiner** `system:ack,bank:DBMS-F-10/f1`: Okay, thanks. If the DBMS has to abort one transaction to break a deadlock, how should it choose which one?
- **[02:33.4] Student**: So, laced work, in other words laced cost, since voids carving the same transaction repeatedly.
- **[02:40.3] Examiner** `system:ack,bank:DBMS-F-05`: Noted. Explain first, second and third normal form in simple terms.
- **[02:45.5] Student**: okay, so atomic, in other words one nf, which means full functional dependency in other words 2nf, for example, in the real way reservation system this matters because thousands of people book at the same time
- **[03:00.3] Examiner** `system:ack,bank:DBMS-F-05/f1`: Right. How is BCNF stricter than third normal form?
- **[03:04.7] Student**: So every determinant must be a candidate slash super key also, 3nf allows a prime attribute to depend on the non key for example, in the real way reservation system this matters because thousands of people book at the same time.
- **[03:19.1] Examiner** `system:ack,bank:DBMS-F-07`: Okay. Explain the different types of joins: inner join, left outer join and full outer join.
- **[03:25.9] Student**: so inner join returns only matching, all rows from both tables also, all rows from left, in other words left join returns all rows of the left table plus matches, nulls were no match, also, full outer join. join returns all rows of both tables, a simple example is a banking app. we're getting this wrong means money goes missing.
- **[03:47.6] Examiner** `system:ack,bank:DBMS-F-07/f1`: Alright. How would you find students who have not enrolled in any course using a join?
- **[03:53.3] Student**: okay, so left join and romance, because we're enrollment key as null
- **[03:58.8] Examiner** `system:ack,bank:DBMS-F-14`: Thank you. What is a dirty read? Which isolation level prevents it?
- **[04:03.9] Student**: So, reading data written by an uncommitted transaction, also that transaction may roll back, so the value never existed. A simple example is a banking app, where getting this wrong means money goes missing.
- **[04:17.8] Examiner** `system:ack,bank:DBMS-F-14/f1`: I see. What is a phantom read, and why doesn't row locking alone prevent it?
- **[04:22.8] Student**: Neuros matching a condition appear on Ray Quiri because needs range/predicate/gap blocks are serializable, you can see this in practice in POST. STG, R-E-S-Q-L and M-Y-S-Q-L, which both handle it this way.
- **[04:38.5] Examiner** `system:ack,system:depth_intro,bank:DBMS-D-05`: Got it. Let's go a little deeper into SQL. How does a query optimiser decide how to execute a SQL query? What information does it use?
- **[04:48.0] Student**: Generates alternative plans because estimates cost of each plan and picks the cheapest and that is why use statistics. A simple example is a banking app. We're getting this wrong means money goes missing.
- **[05:00.4] Examiner** `system:ack,bank:DBMS-D-05/f2`: Okay, thanks. Why might the optimiser ignore an index that exists on the filtered column?
- **[05:06.9] Student**: Right, most rows, in other words, low selectivity, because like percent, in other words function on the column slash type mismatch prevent use.
- **[05:17.0] Examiner** `system:ack,bank:DBMS-D-05/f1`: Noted. What happens if the table statistics are badly out of date?
- **[05:21.7] Student**: So, wrong estimate, in other words, wrong cardinality estimates led to bad plans because fix with analyze/update statistics
- **[05:30.9] Examiner** `system:ack,bank:DBMS-D-06`: Right. Explain the steal and no-force policies in buffer management and why they make recovery need both undo and redo.
- **[05:38.4] Student**: sure, steal, also, not flushed, in other words, no-force, also fewer rights, in other words give better performance/flex ability for the buffer manager, you can see this in practice in POST. .
- **[05:57.0] Examiner** `system:ack,bank:DBMS-D-06/f1`: Okay. What if a system used no-steal and force instead? What would be simpler and what would be worse?
- **[06:03.8] Student**: ok, so no undo or redo need it, also memory in other words but purper for formats.
- **[06:10.3] Examiner** `system:ack,bank:DBMS-D-03`: Alright. What is a lossless-join decomposition, and how do you check that splitting a table did not lose information?
- **[06:18.3] Student**: joining the parts returns exactly the original relation and that is why intersection in other words comes common attributes must be a key of at least one part, you can see this in practice in POSDGR-ESQL and MYSQL, which both handle it this this way
- **[06:34.2] Examiner** `system:ack,bank:DBMS-D-03/f1`: Thank you. Why can decomposing to BCNF lose dependency preservation, while 3NF never does?
- **[06:41.4] Student**: ok, so BCNF splitting may separate attributes of the thumb functional dependency across tables, also free NF synthesis key keeps every dependency in some table in our college of project, for instance, we actually run into exactly this.
- **[06:57.5] Examiner** `system:ack,bank:DBMS-D-02`: I see. Compare the isolation levels read committed, repeatable read and serializable. What does each one allow, and what does it cost?
- **[07:06.3] Student**: read committed, also same value in other words repeatable read since serializable a simple example is a banking app We're getting this wrong means money goes missing
- **[07:17.7] Examiner** `system:ack,bank:DBMS-D-02/f1`: Got it. Why don't databases just use serializable everywhere by default?
- **[07:23.4] Student**: So, so in other words, repeatable
- **[07:27.2] Examiner** `system:ack,bank:DBMS-D-04`: Okay, thanks. How does multiversion concurrency control (MVCC) work, and why do databases like PostgreSQL use it?
- **[07:37.1] Student**: Ok, so new version, in other words writers create new versions instead of overwriting because consistent view, in other words readers you consistent snapshot, and and that is why readers and writers, in other words readers don't block writers and writers don't block readers. For example, in the real way reservation system this matters because thousands of people book at the same time.
- **[07:59.1] Examiner** `system:ack,bank:DBMS-D-04/f1`: Noted. What problem appears if old versions are never cleaned up?
- **[08:03.7] Student**: Sure, table bloat, sewer scans, wasted storage
- **[08:08.5] Examiner** `system:ack,bank:DBMS-D-04/f2`: Right. Can two concurrent MVCC transactions still conflict? Give an example.
- **[08:14.4] Student**: Ok, so write write conflict on the same row, or write skew
- **[08:19.8] Examiner** `system:ack,bank:DBMS-D-07`: Okay. What is a weak entity? How is it identified and represented in tables?
- **[08:25.7] Student**: cannot be uniquely identified by its own attributes, so so discriminator, in other words identified by an owner/strong entity plus a partial key. For example, in the real way reservation system this matters because fied thousands of people book at the same time.
- **[08:42.1] Examiner** `system:ack,bank:DBMS-D-07/f2`: Alright. Why does deleting the owner usually delete the weak entity rows too?
- **[08:47.5] Student**: Weak entity has no meaning/identity without its owner
- **[08:51.9] Examiner** `system:ack,bank:DBMS-D-07/f1`: Thank you. Give an example of a weak entity from a college or hospital system.
- **[08:57.7] Student**: Okay, so concrete example such as dependent of employee room and building installment of the loan
- **[09:04.4] Examiner** `system:ack,system:to_applied,bank:DBMS-A-03`: I see. Now let's try a practical problem. Please think out loud as you work through it. Design the tables for a library system: members borrow books, a book can have many copies, and we must know who has which copy and when it is due. Talk me through your tables and keys.
- **[09:20.6] Student**: Let me think about this step by step. First I would reproduce the problem. Right, copy table, in other words separate book, which means student table. In other words member table with member underscore it primary key. also, return date, in other words loan slash barrow table linking member and copy with dates. for example, in the real way reservation system this matters because thousands of people book at the same time, then I would test the fix under the same condition. the
- **[09:52.0] Examiner** `system:ack,bank:DBMS-A-03/f2`: Got it. What if the library now wants to let members reserve a book that is currently out? What changes?
- **[09:58.9] Student**: Ok, so in reservation table referencing member and book in our college of project, for instance, we actually run into exactly this.
- **[10:08.8] Examiner** `system:ack,bank:DBMS-A-03/f1`: Okay, thanks. How would you find all overdue books with your design?
- **[10:13.8] Student**: Okay, so do date less than today in other words, Quivy loan's well where do date to date and return underscore data's null.
- **[10:21.7] Examiner** `system:ack,bank:DBMS-A-04`: Noted. This query is supposed to show each department with its number of employees, including departments with no employees. But departments with zero employees are missing. Explain why and how to fix it.
- **[10:34.1] Student**: Let me think about this step by step. First I would reproduce the problem. So inner join removes departments without matching employees. Also, left outer join, in other words use left join from departments. For example, in the real way reservation system this matters because thousands of people book at the same time, then I would test the fix under the same condition. the
- **[11:01.2] Examiner** `system:ack,bank:DBMS-A-04/f1`: Right. After switching to LEFT JOIN but keeping COUNT(*), what number would an empty department show, and why?
- **[11:08.8] Student**: Sure, shows one, because the null field row still counts as a row.
- **[11:14.1] Examiner** `system:ack,bank:DBMS-A-05`: Okay. A payment app moves money from wallet A to wallet B with two separate UPDATE statements and no transaction. Occasionally money disappears. Reason aloud about the causes and design a fix.
- **[11:26.0] Student**: Let me think about this step by step. First I would reproduce a problem, right? crash between, in other words no atomicity and that is why start transaction, in other words wrap both in a single transaction with begin/commit, roll back on failure Also check constraint, in other words check sufficient balance atomically A simple example is a banking app, where getting this wrong means money goes missing Then I would test the fix under the same conditions and measure again to confirm it
- **[11:55.3] Examiner** `system:ack,bank:DBMS-A-05/f1`: Alright. What if wallets A and B live in two different databases? Can one transaction still cover both?
- **[12:02.2] Student**: Two-phase commit, in other words needs distributed transaction in our college of project, for instance, we actually ran into exactly this.
- **[12:12.0] Examiner** `system:ack,bank:DBMS-A-05/f2`: Thank you. Why might two transfers between the same wallets in opposite directions deadlock, and how can you prevent it?
- **[12:19.7] Student**: Sure each locks one wallet and waits for the other, also same order, in other words always lock rows in the fixed order, you you can see this in practice in POSDGRESQL and MYSQL which both handle it this way.
- **[12:34.3] Examiner** `system:ack,system:to_wrapup,system:wrapup_q`: I see. We're nearly done. Is there anything you'd like to add or clarify about any of your answers?
- **[12:41.4] Student**: No, I think I've covered everything, thank you.
- **[12:45.2] Examiner** `system:close`: Thank you. That's the end of your viva. Your answers will be reviewed and your faculty will share the results. You can close this window now.

## Scores

| Dimension | Score | Reason |
|---|---|---|
| conceptual | 4 / 5 | Covered 29 of 39 key points across 11 questions; strongest at 03:19 (SQL), weakest at 02:06 (Concurrency). |
| depth | 4 / 5 | 17 of 19 follow-ups answered with sound reasoning; reasons or examples given in 69% of answers. |
| problem_solving | 5 / 5 | Applied problem: identified 3 of 4 expected steps (separate Book, member table with member_id primary key). |
| practical | 5 / 5 | Project described concretely (named react, sql); own role explained. |
| communication | 5 / 5 | 100% of answers on the question; 69% organised with reasons, steps or examples. |

## Feedback

- Strong on sql: at 03:25 you covered inner join returns only matching rows from both tables, left join returns all rows of the left table plus matches, NULLs where no match, full outer join returns all rows of both tables.
- Good reasoning on follow-up questions: you explained why, not just what.
- In the applied problem you identified separate Book; also consider foreign keys from Loan to Member and Copy; Copy to Book.
- Your answers were clear and organised.
