import Request from "@/lib/request/Request.ts";
import { request, parseRegionFromToken } from "@/api/controllers/core.ts";
import logger from "@/lib/logger.ts";

export default {
  prefix: "/v1/history",

  post: {
    "/poll": async (request_: Request) => {
      const { history_ids, submit_ids, token: refreshToken } = request_.body;

      if (!refreshToken) {
        throw new Error("Missing token");
      }
      if (!history_ids?.length && !submit_ids?.length) {
        throw new Error("Missing history_ids or submit_ids");
      }

      const regionInfo = parseRegionFromToken(refreshToken);
      logger.info(
        `[history/poll] polling ${(history_ids || submit_ids).length} ids ` +
          `(region=${regionInfo.region})`
      );

      const result = await request(
        "post",
        "/mweb/v1/get_history_by_ids",
        refreshToken,
        {
          data: {
            history_ids: history_ids || [],
            submit_ids: submit_ids || [],
          },
        }
      );

      return result;
    },
  },
};
