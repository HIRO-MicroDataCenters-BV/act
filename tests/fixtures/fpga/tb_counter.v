module tb_counter;
    reg clk;
    reg reset;
    wire [3:0] count;

    counter dut (.clk(clk), .reset(reset), .count(count));

    always #5 clk = ~clk;

    initial begin
        clk = 0;
        reset = 1;
        #12 reset = 0;
        repeat (5) begin
            @(posedge clk);
            #1 $display("Test: counter=%0d", count);
        end
        $display("DONE");
        $finish;
    end
endmodule
