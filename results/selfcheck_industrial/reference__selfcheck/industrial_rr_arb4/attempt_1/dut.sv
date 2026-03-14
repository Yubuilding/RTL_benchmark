module rr_arb4(input logic clk, input logic rst_n, input logic [3:0] req, output logic [3:0] grant);
  logic [1:0] last_grant;

  function automatic logic [3:0] pick_grant(input logic [3:0] req_i, input logic [1:0] last_i);
    logic [3:0] result;
    int offset;
    int idx;
    begin
      result = 4'b0000;
      for (offset = 1; offset <= 4; offset++) begin
        idx = (last_i + offset) % 4;
        if ((result == 4'b0000) && req_i[idx])
          result[idx] = 1'b1;
      end
      pick_grant = result;
    end
  endfunction

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      grant <= 4'b0000;
      last_grant <= 2'd3;
    end else begin
      grant <= pick_grant(req, last_grant);
      case (pick_grant(req, last_grant))
        4'b0001: last_grant <= 2'd0;
        4'b0010: last_grant <= 2'd1;
        4'b0100: last_grant <= 2'd2;
        4'b1000: last_grant <= 2'd3;
        default: last_grant <= last_grant;
      endcase
    end
  end
endmodule
