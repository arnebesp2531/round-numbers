# Main Question: 

What does it mean for a number to be “round”, what makes a number round, and what numbers are more round than others?

R(x) \= roundness factor of x.

# Different Hypotheses:

1. Our concept of “roundness” is based on the units we have around us all the time in the real world, like currency and time. Therefore, the numbers we determine to be more round are numbers which are easier to create with the fewest number of additive coins/bills. This means R($0.35) \> R($0.49), and R(15) \> R(16).  
2. We tend to think geometrically when we are given numbers, so our gut reaction to a number’s roundness will correlate directly with the number of factors within that number. Think of a rectangle with area LxW, or a square. The more compact (nearest to square) arrangement of the sides, the more round it seems. Therefore, prime numbers feel the least round, and very composite numbers feel the most round (possibly with special emphasis on multiples of 5/10, per hypothesis \#1). Under this hypothesis, perfect squares would feel very round, so R(36) \> R(35), and R(16) \< R(15).  
3. Certain numbers have more “roundness gravity” than others, meaning that even when we take into account both \#1 and \#2, very “heavy” numbers like 10 or 50 or 100 will disproportionately de-round the numbers within their gravitational field. This gravitational field increases as the size of the number increases. This means although 96 is a very composite number, much of its roundness is transferred onto 100 because of its proximity to the heavy number.  
4. Our idea of roundness correlates directly with the number of characters it takes to spell the number in Roman Numerals. This theory accounts for both the additive nature of \#1, as well as the heavy numbers of \#2. 

# Methods:

* Make a streamlit app within snowflake which presents pairs of numbers to users and asks them to select which number is “more round”. Enough direct comparisons will eventually allow us to determine the relative roundness of each number in the given range.  
* User flow of streamlit app  
  * Intro page  
    * Shows a brief description of what we are trying to determine.  
    * To begin, “choose a random number 1-100” (for fun, later). Then also have them “choose a random number 1-100 which you believe the fewest number of other people will also choose.”  
  * Comparison test  
    * The user is presented with a set of random pairs of numbers 1-100. Each pair is randomly ordered. Each user would get a different screen when they load the app.   
    * Ask the user to select based on their immediate gut intuition, not conscious thought (system 1 \-- reliance on heuristics). There are no wrong answers.   
    * When the user selects one answer for each pair on the page, then give them the option to submit another page until the user chooses to be done inputting numbers or they have submitted 100 total comparisons (?). I don’t necessarily want to limit the amount of comparisons for one user (more data \= good), but I don’t want one user’s definition of roundness outweighing other user’s definitions...  
    * Here is a very quick & dirty mock-up of what the comparison test page could look like:  
      * [wireframe.png]
  * Short survey form  
    * Ask the user to define in their own words what “roundness” means to them.   
    * What factors went into your intuitive gut reaction?  
    * What do you classify as the least round number 1-100?  
    * Choose a random number 1-100. Compare their post-session numbers with their pre-session numbers. Is the group/individual more or less likely to choose numbers which the group/individual determines to be round?    
  * Results page  
    * Visualize the results, with a histogram from 1-100 showing the “roundness” as the vertical height. Behind the scenes, we would calculate roundness of a number based on all of the comparisons it either won or lost. For completeness, we would need 100C2=4950 comparisons. Perhaps a simpler metric would be “% of comparisons won”. There are likely additional existing metrics which provide a more robust comparison.   
    * Show the overall Roundest Number, and the Least Round Number based on the groups selection.  
    * Also include a little analysis of the “choose a random number” questions.  
* Behind the scenes  
  * Claude code will write all of the code for the streamlit code, but then I will upload all code into Streamlit in snowflake to execute the code and run it there. I will also hand-run any snowflake setup scripts, so Claude can output those as sql files in the project.   
  * We need to set up a snowflake table to log the comparison results. Not sure on the best way to do this... Should we log the actual comparison itself (with a graph or something?, or a first\_number, second\_number, winning\_number, losing\_number), or should we just the numbers table like number, num\_comparisons, num\_wins, num\_losses. Or do both of these to enable different metrics? Or have a pure fact table that feeds into a larger results table?  
  * Could we use ai\_classify() to categorize the user’s free text explanation of roundness into a few different camps? Then we could display some sort of visualization on this classification data at the end as well.  
  * How many comparisons would we need to make in order to have some confident metrics on the roundness of numbers?  
  * 


  
Future experiments (not to be implemented in this version of the app):

1. Can we subconsciously switch the user into different modes of roundness by subtly changing things to prime the user to think of money, geometry, or roman numerals? Split the test group evenly into multiple groups:  
   1. Control group \-- just text.  
   2. Show a cartoon depiction of a stack of dollar bills next to the title.  
   3. Show a couple diagrams of polygons or subdivided rectangles.  
   4. Show a cartoon depiction of a Roman soldier.

