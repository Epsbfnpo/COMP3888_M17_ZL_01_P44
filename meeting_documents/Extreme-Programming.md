# Extreme Programming (XP)

## 1. What is Extreme Programming?

**Extreme Programming (XP)** is an **Agile software development methodology** designed for projects where requirements may change frequently.

XP focuses on:

* Short development cycles
* Frequent customer feedback
* Continuous testing
* Simple design
* High-quality code
* Responding quickly to changing requirements

The name **Extreme Programming** comes from taking useful software development practices and applying them very consistently.

For example:

* If **testing** is useful → test continuously.
* If **code review** is useful → use pair programming.
* If **integration** is useful → integrate frequently.
* If **customer feedback** is useful → involve customers throughout development.
* If **simple design** is useful → keep the design as simple as possible.

---

## 2. Traditional Development vs XP

A traditional development process may look like:

```text
Requirements
    ↓
Design
    ↓
Implementation
    ↓
Testing
    ↓
Release
```

The problem is that the customer may not see the finished system for months.

If requirements were misunderstood, the team may discover the problem very late.

XP instead uses short cycles:

```text
Small Requirement
    ↓
Plan
    ↓
Code
    ↓
Test
    ↓
Integrate
    ↓
Customer Feedback
    ↓
Improve
    ↓
Repeat
```

The goal is to **discover problems and changing requirements early**.

---

# 3. XP and Agile

XP is part of the broader **Agile** family.

Agile is a general philosophy that emphasizes:

* Working software
* Customer collaboration
* Responding to change
* Frequent feedback
* Incremental development

XP provides specific practices for achieving these goals.

> **Agile = general philosophy**
>
> **XP = specific Agile methodology**

Other Agile approaches include frameworks such as **Scrum**.

A major difference is that Scrum focuses heavily on organizing and managing development work, while XP places strong emphasis on **software engineering practices**.

---

# 4. The Five Values of XP

XP is based on five core values:

1. Communication
2. Simplicity
3. Feedback
4. Courage
5. Respect

## 4.1 Communication

Developers, customers, testers, and other team members should communicate frequently.

Many software problems happen because developers misunderstand what users actually need.

For example:

> Customer: "That's not what I meant."

XP attempts to discover these misunderstandings as early as possible.

Practices such as **pair programming**, **customer involvement**, and frequent planning improve communication.

---

## 4.2 Simplicity

XP encourages developers to create the **simplest solution that satisfies the current requirements**.

Developers should avoid implementing features merely because they *might* be useful in the future.

This idea is closely related to:

> **YAGNI — You Aren't Gonna Need It**

For example, if the current requirement is simply:

```text
Student
- ID
- Name
- Email
```

developers should not immediately create complicated support for future functionality that nobody currently requires.

Overengineering makes software harder to understand, test, and change.

---

## 4.3 Feedback

XP tries to create very short **feedback loops**.

Feedback can come from several sources.

### Tests

```text
Write code
    ↓
Run tests
    ↓
Immediate feedback
```

### Customers

```text
Release feature
    ↓
Customer uses feature
    ↓
Customer feedback
```

### Other Developers

```text
Pair programming
    ↓
Immediate review
    ↓
Feedback
```

The faster feedback arrives, the faster the team can correct mistakes.

---

## 4.4 Courage

Developers need the courage to:

* Change existing code
* Delete unnecessary code
* Refactor poor designs
* Admit when an approach isn't working
* Change requirements when necessary
* Tell customers when something is unrealistic

Automated tests help developers make changes confidently because tests can detect whether existing functionality has been broken.

---

## 4.5 Respect

Everyone on the development team should respect each other's work.

This includes:

* Listening to other developers
* Following agreed coding standards
* Not deliberately leaving broken code
* Taking responsibility for code quality
* Respecting customer requirements

---

# 5. User Stories

XP commonly represents requirements using **user stories**.

A common format is:

```text
As a [type of user],
I want [functionality],
so that [benefit].
```

Example:

> As a student, I want to search courses by subject so that I can find relevant electives.

Another example:

> As a customer, I want to reset my password so that I can regain access to my account.

User stories are deliberately short.

They describe **what the user wants**, rather than providing a massive technical specification.

Developers and customers can discuss the details when the story is selected for implementation.

---

# 6. The Planning Game

The **Planning Game** is an XP planning practice.

It combines knowledge from both customers and developers.

## Customer / Business Side

The customer decides:

* Which features are important?
* Which features should be built first?
* What provides the greatest business value?

## Developer Side

Developers determine:

* How difficult is the feature?
* How much effort will it require?
* What are the technical risks?

Example:

| User Story | Priority | Estimated Effort |
| ---------- | -------- | ---------------- |
| User login | High     | 2 days           |
| Dark mode  | Low      | 2 days           |
| Checkout   | High     | 5 days           |

The customer determines **business priority**, while developers determine **technical estimates**.

Together, they decide what should be developed next.

---

# 7. Small Releases

XP encourages teams to release working software in **small increments**.

Instead of:

```text
6 months development
        ↓
One huge release
```

XP prefers:

```text
Small feature
    ↓
Release
    ↓
Feedback
    ↓
Small feature
    ↓
Release
    ↓
Feedback
```

Small releases provide several benefits:

* Customers receive useful functionality earlier.
* Problems are discovered earlier.
* Requirements can change more easily.
* Each release carries less risk.

---

# 8. Test-Driven Development (TDD)

One of the most important XP practices is **Test-Driven Development (TDD)**.

The basic TDD process is:

> **Red → Green → Refactor**

## Step 1: Red

Write a test before implementing the feature.

```python
def test_add():
    assert add(2, 3) == 5
```

The test initially fails because `add()` has not been implemented.

## Step 2: Green

Write the minimum amount of code required to make the test pass.

```python
def add(a, b):
    return a + b
```

The test should now pass.

## Step 3: Refactor

Improve the code while keeping its behavior unchanged.

Then run the tests again.

The complete cycle is:

```text
Write Test
    ↓
Test Fails
    ↓
Write Code
    ↓
Test Passes
    ↓
Refactor
    ↓
Repeat
```

This creates a growing automated test suite that helps protect the system when developers make future changes.

---

# 9. Refactoring

**Refactoring** means improving the internal structure of code **without changing its external behavior**.

For example:

### Before

```python
if user.age >= 18 and user.country == "AU":
    ...
```

If this condition appears repeatedly, it could be refactored.

### After

```python
def is_eligible(user):
    return user.age >= 18 and user.country == "AU"
```

The behavior hasn't changed.

However, the code may now be:

* Easier to understand
* Easier to reuse
* Easier to maintain
* Easier to test

XP encourages **continuous refactoring** rather than waiting until the code becomes extremely difficult to maintain.

TDD and refactoring therefore work closely together:

```text
Automated Tests
      ↓
Safer Changes
      ↓
Refactoring
      ↓
Cleaner Code
```

---

# 10. Pair Programming

XP strongly encourages **pair programming**.

Two developers work together on the same problem.

## Driver

The **driver** controls the keyboard and writes the code.

## Navigator

The **navigator** reviews the work and thinks about:

* Possible errors
* Requirements
* Design
* Edge cases
* Alternative solutions

```text
       Computer
          ↑
          │
   ┌──────┴──────┐
   │             │
 Driver       Navigator
 writes       reviews
 code         and plans
```

Developers regularly switch roles.

Pair programming can provide:

* Continuous code review
* Fewer mistakes
* Better knowledge sharing
* Better design discussions
* Reduced dependence on individual developers

---

# 11. Continuous Integration

**Continuous Integration (CI)** means developers integrate their changes into the shared codebase frequently.

Without frequent integration:

```text
Developer A works for weeks ───┐
                               ├── Merge → Many conflicts
Developer B works for weeks ───┘
```

This can create **integration hell**.

XP instead encourages:

```text
Small Change
    ↓
Integrate
    ↓
Run Tests
    ↓
Small Change
    ↓
Integrate
    ↓
Run Tests
```

Modern CI systems can automatically:

1. Detect new code
2. Build the application
3. Run automated tests
4. Report success or failure

```text
Developer Pushes Code
          ↓
       CI Server
          ↓
    Build Software
          ↓
      Run Tests
       ↙     ↘
    PASS     FAIL
```

---

# 12. Simple Design

XP encourages the **simplest design that satisfies the current requirements**.

Developers should avoid complicated architecture based entirely on possible future requirements.

For example, if the application currently only needs to export PDF files, developers should avoid creating an extremely complicated abstraction supporting dozens of theoretical future export formats unless there is a real requirement for it.

This follows the principle:

> **YAGNI — You Aren't Gonna Need It**

Simple design makes software easier to:

* Understand
* Test
* Modify
* Refactor
* Maintain

---

# 13. Collective Code Ownership

XP uses **collective code ownership**.

Code does not belong to individual programmers.

Instead of:

```text
Alice's code
Bob's code
John's module
```

XP treats the system as:

```text
Team's Codebase
```

Any developer can:

* Fix bugs
* Refactor code
* Improve design
* Add functionality

This prevents situations where the team cannot change something because only one person understands or "owns" that code.

Automated testing and coding standards make collective ownership safer.

---

# 14. Coding Standards

Because everyone can modify the codebase, the team should follow common **coding standards**.

Standards may cover:

* Naming conventions
* Formatting
* File organization
* Documentation
* Architecture conventions
* Function design

For example, developers should avoid inconsistent naming such as:

```python
calculate_total()
CalculateTotal()
calcTotal()
CALCULATE_TOTAL()
```

unless the language or agreed style specifically requires it.

The goal is for the codebase to look as though it was created by **one consistent team**.

---

# 15. Sustainable Pace

XP originally described this as the **40-hour week**.

Today, it is often better understood as maintaining a **sustainable pace**.

Constant overtime can create:

```text
Long Working Hours
        ↓
      Fatigue
        ↓
    More Mistakes
        ↓
     More Bugs
        ↓
More Time Fixing Bugs
        ↓
   More Overtime
```

XP argues that developers should work at a pace they can maintain over a long period.

This helps preserve:

* Productivity
* Code quality
* Concentration
* Team morale

---

# 16. On-Site Customer

Traditional XP recommends having an **on-site customer** who is readily available to the development team.

The customer can quickly answer questions such as:

> What should happen if payment fails?

> Which feature is more important?

> Does this implementation satisfy the requirement?

The customer does not necessarily need to physically sit next to developers in modern teams.

The important idea is that developers should have **quick access to someone who understands the business requirements**.

This may be:

* Customer representative
* Product owner
* Business analyst
* Domain expert

---

# 17. System Metaphor

Traditional XP includes the idea of a **system metaphor**.

A system metaphor provides the team with a simple shared way of understanding how the system works.

For example, a university enrollment system might be described as:

> "A digital course marketplace."

The metaphor gives developers and customers a common mental model for discussing the system.

This practice is less emphasized in many modern XP implementations but remains part of traditional XP theory.

---

# 18. Acceptance Testing

Acceptance tests determine whether a feature actually satisfies the customer's requirement.

A **unit test** might ask:

> Does this function return the correct value?

An **acceptance test** asks:

> Does this feature actually do what the customer requested?

For example, consider this user story:

> As a customer, I want to receive confirmation after placing an order.

Acceptance criteria could be:

```text
Given:
The customer has items in their cart

When:
The customer successfully completes payment

Then:
An order is created
AND
A confirmation is displayed
AND
A confirmation email is sent
```

Acceptance testing helps determine whether a user story is truly complete.

---

# 19. How XP Practices Work Together

XP practices are designed to reinforce each other.

```text
User Stories
      ↓
Planning Game
      ↓
Small Iteration
      ↓
TDD ←→ Pair Programming
      ↓
Simple Design
      ↓
Refactoring
      ↓
Continuous Integration
      ↓
Acceptance Testing
      ↓
Small Release
      ↓
Customer Feedback
      ↓
Next Iteration
```

The result is a **continuous feedback loop**.

---

# 20. Example of XP in Practice

Imagine a team developing a food delivery application.

The customer creates several user stories:

### Story 1

> As a user, I want to search restaurants so that I can find somewhere to order food.

### Story 2

> As a user, I want to add food to my cart so that I can prepare an order.

### Story 3

> As a user, I want to pay for my order so that the restaurant can process it.

### Story 4

> As a user, I want to track my delivery so that I know when my food will arrive.

The customer prioritizes the stories.

Developers estimate the effort required.

The team selects a small number of stories for the next iteration.

For the shopping cart story, developers might first create a test:

```text
Given:
The cart is empty

When:
The user adds a burger

Then:
The cart contains one burger
```

The developers then:

1. Write the test.
2. Implement the feature using pair programming.
3. Run the tests.
4. Refactor the implementation.
5. Integrate the code.
6. Run all automated tests.
7. Demonstrate the feature to the customer.

The customer might then say:

> Users should also be able to change the quantity of an item.

Instead of treating this change as a failure of the original plan, XP expects requirements to evolve.

The new requirement becomes another user story that can be prioritized for a future iteration.

---

# 21. Advantages of XP

## Responds Well to Change

XP expects requirements to evolve rather than assuming everything can be determined at the beginning.

## High Code Quality

Practices such as:

* TDD
* Refactoring
* Pair programming
* Continuous integration

help maintain software quality.

## Rapid Feedback

Problems can be discovered within minutes, hours, or days rather than months.

## Reduced Integration Risk

Frequent integration reduces the chance of massive merge problems near the end of development.

## Knowledge Sharing

Pair programming and collective ownership spread knowledge throughout the team.

## Customer Involvement

Customers continuously influence the product and can identify incorrect requirements early.

---

# 22. Disadvantages of XP

## Requires Customer Involvement

XP works best when someone representing the customer is regularly available.

## Pair Programming Can Be Difficult

Some developers dislike pair programming, and organizations may initially see two developers working on one problem as inefficient.

## Requires Discipline

Practices such as TDD, continuous integration, and refactoring must be followed consistently.

## Documentation May Receive Less Emphasis

XP emphasizes working software and communication.

However:

> **XP does not mean "no documentation."**

Documentation should still be produced when it provides useful value.

## Large or Distributed Teams

Some XP practices can become more difficult when teams are extremely large or spread across different locations and time zones.

## Highly Regulated Systems

Some systems require extensive upfront documentation, formal verification, or approval processes.

XP practices can still be useful, but they may need to be combined with additional processes.

---

# 23. XP vs Scrum

Both XP and Scrum belong to the Agile family, but they emphasize different things.

| Feature                | XP                      | Scrum                          |
| ---------------------- | ----------------------- | ------------------------------ |
| Type                   | Agile methodology       | Agile framework                |
| Main focus             | Software engineering    | Team/project management        |
| Iterations             | Short iterations        | Usually 1–4 week sprints       |
| TDD                    | Strongly emphasized     | Not required                   |
| Pair programming       | Strongly associated     | Not required                   |
| Refactoring            | Core practice           | Not prescribed                 |
| Continuous integration | Core practice           | Not prescribed                 |
| Customer involvement   | Strong                  | Product Owner represents needs |
| Planning               | Stories + Planning Game | Product/Sprint Backlogs        |
| Code ownership         | Collective ownership    | Not specifically prescribed    |

XP and Scrum can also be combined.

For example:

```text
Scrum
    ↓
Organizes the team's work

XP
    ↓
Provides engineering practices
```

A team could therefore use Scrum for sprint planning and XP practices such as:

* TDD
* Pair programming
* Continuous integration
* Refactoring

during development.

---

# 24. XP Summary

Extreme Programming can be summarized as:

> **XP is an Agile software development methodology designed to handle changing requirements through short development cycles, close customer involvement, frequent feedback, automated testing, and disciplined engineering practices.**

## Important XP Practices

1. **User Stories** — describe requirements from the user's perspective.
2. **Planning Game** — customers prioritize while developers estimate.
3. **Small Releases** — release working software frequently.
4. **TDD** — write tests before implementation.
5. **Pair Programming** — two developers work together.
6. **Refactoring** — continuously improve code structure.
7. **Simple Design** — implement the simplest solution needed now.
8. **Continuous Integration** — integrate and test code frequently.
9. **Collective Code Ownership** — the entire team owns the codebase.
10. **Coding Standards** — maintain consistent coding conventions.
11. **Sustainable Pace** — avoid continuous overtime.
12. **Customer Involvement** — obtain rapid business feedback.
13. **Acceptance Testing** — confirm features satisfy customer requirements.
14. **System Metaphor** — maintain a shared understanding of the system.

---

# 25. Quick Exam Revision

### What is XP?

An Agile methodology emphasizing **rapid feedback, adaptability, customer involvement, testing, and high-quality code**.

### Five XP Values

```text
Communication
Simplicity
Feedback
Courage
Respect
```

### TDD

```text
RED
Write failing test
    ↓
GREEN
Write code to pass
    ↓
REFACTOR
Improve code
    ↓
Repeat
```

### Pair Programming

```text
Driver
→ writes code

Navigator
→ reviews and thinks ahead

Roles switch regularly
```

### Planning Game

```text
Customer
→ Business priority

Developers
→ Technical effort

Together
→ Decide what to build
```

### Main XP Development Loop

```text
User Story
    ↓
Plan
    ↓
Write Tests
    ↓
Implement
    ↓
Refactor
    ↓
Integrate
    ↓
Acceptance Test
    ↓
Small Release
    ↓
Customer Feedback
    ↓
Repeat
```

### Core Idea

> **Do not try to predict everything at the beginning. Build small amounts of working software, test continuously, obtain feedback quickly, and adapt.**
